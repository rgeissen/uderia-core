# Agent-Pack Governance Architecture (Tier 3.2 / P7)

**Status:** implemented on `main` (checked against code 2026-10-30). Makes agent packs **governed data products** (assessment
requirement **P7** — only Databricks was GREEN). Design: `LEAPFROG_ROADMAP.md Epic 3.2`.

---

## 1. The idea

An agent pack is already a versioned/owned/shareable bundle. Governance adds the **release
lifecycle** and **service-level** metadata that make it a *governed data product*:

> **draft → propose → approve → publish (release) → deprecate**, plus an **SLA tier**.

It **composes with the Trust Membrane (Tier 5.2)**: the *publish gate maps the pack's trust
state to conformance* — a *governed* data product must be *trusted* (🟢 → conformant · 🟠 →
partial · ⚪ → nonconformant). Default policy (no approval, block-untrusted OFF) reproduces
today's behaviour byte-for-byte.

## 2. Pure reuse — the Tier-4.3 state machine

The decision logic is **not re-implemented**. `core/governance_lifecycle.py` (the
storage-agnostic Tier-4.3 state machine — `validate_transition`, `GovernancePolicy`,
`Decision`, `build_audit`, separation-of-duties, the conformance publish gate) is reused
verbatim. `core/pack_governance.py` is only the **storage + effects glue**, mirroring
`core/ontology_governance.py`.

## 3. Components

| Piece | Responsibility |
|---|---|
| `core/pack_governance.py` | Orchestrator: reads/writes lifecycle state on `agent_pack_installations` (additive self-applying `ALTER`s), maps pack **trust → conformance** for the publish gate, calls `validate_transition`, audits every attempt (`pack_lifecycle_audit`), emits an OTLP-shaped governance span, and on **deprecate** archives the pack's sessions. `transition_pack(...)` (async) + `get_pack_lifecycle` + `get_pack_audit` + `set_sla` + `get_pack_governance_policy`. |
| `api/agent_pack_routes.py` | `GET /v1/agent-packs/<id>/lifecycle` · `POST …/lifecycle/<action>` (denied → 409, audited) · `POST …/sla` (instant SLA set) · `GET/PUT /v1/agent-packs/governance/policy` (admin). **Enforcement:** a *deprecated* pack can no longer be **forked** (403). |
| `static/js/handlers/agentPackHandler.js` | The **Govern dialog** (`openPackGovernDialog`) — a world-class, frictionless lifecycle UX. |
| `static/js/adminManager.js` + `templates/index.html` | The admin **Agent Pack Governance** policy block (require-approval · require-trust). |

## 4. Storage (additive, self-applying)

On `agent_pack_installations`: `lifecycle_status` (draft/published/deprecated),
`approval_status` (none/pending/approved), `sla_tier`, `proposed_at`, `approved_by`,
`approved_at`, `released_at`, `deprecated_at` — all added by `ALTER` on first use. Audit rows
in the self-applying `pack_lifecycle_audit` table, which is **append-only** (Oct 2026 federal
hardening): `_connect` installs the `core/audit_integrity.install_triggers` UPDATE/DELETE triggers on
it, so an audit row cannot be altered or deleted inside `TDA_AUDIT_RETENTION_DAYS` (floor 365). Policy in `system_settings`
(`pack_require_approval` / `pack_approver_tiers` / `pack_block_untrusted_publish`).

## 5. The Govern dialog (UX)

A process-focused, motivating experience (not a form):
- a **lifecycle journey stepper** (Draft → [Pending → Approved →] Released; deprecated = a
  terminal ribbon) — visible progress;
- a **trust hero** that affirms ("Trusted & ready to release") or nudges ("Not signed yet —
  sign it off first") with a one-click **Sign off →** into the Trust Membrane dialog;
- one obvious **primary CTA** (the recommended next action), destructive **Deprecate** demoted;
- **instant SLA pills** (Gold/Silver/Bronze/None, save on click);
- a clean **activity timeline**; live journey-advance + affirming confirmation per action.

Monochrome SVG icons, theme-aware, motion-respecting, keyboard/Esc, `tda_auth_token`.

## 6. Enforcement + effects

- **Deprecation archives sessions** — the pack's member profiles' sessions are archived via
  `session_manager.archive_sessions_by_profile` (the unified artifact-relationships path).
- **A deprecated pack cannot be forked** (403) — a retired data product is not consumable. The
  check sits in the marketplace fork route (`agent_pack_routes.py`, after the `distribution_mode`
  check); subscribe is not gated by lifecycle.
- Every attempt (allowed or denied) is **audited**; publish/deprecate emit a governance span.

## 7. Guarantees & proof

- **Byte-identical baseline** — default policy = today's behaviour; no attestations/lifecycle
  changes anything until an owner acts.
- **Separation of duties** — when approval is required, the approver must be in an approver
  tier and (by policy) not the owner.
- **Proof** — `test/test_pack_governance.py` (19 assertions: lifecycle persistence, the trust
  publish gate, separation of duties, SLA, audit, baseline) + live-verified end-to-end
  (lifecycle GET, publish with SLA, policy round-trip, deprecation).
