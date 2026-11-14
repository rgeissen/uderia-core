# Ontology Suite — isolated, physically-removable enterprise feature

The **Ontology suite** is one enterprise capability = the **Platform Ontology**
(the "Ontology" nav view — the agent-centric estate map) **+** the **Trust
Membrane** (the agent trust badge computed compositionally over its ingredients).
It is the enterprise differentiator (*"provide the enterprise ontology, and make
agents trusted"*).

Design: `TIER_5_PLATFORM_ONTOLOGY.md` ·
`TIER_5.2_TRUST_MEMBRANE.md`

---

## One switch

The whole suite is gated by a single master switch —
[`core/enterprise.py`](../enterprise.py) `is_enterprise_ontology_enabled()`
(**True today**; one-line flip to `tier == "Enterprise"` later). When off: the
"Ontology" sidebar item isn't rendered, its REST 404s, and the Trust Membrane
isn't registered.

---

## Distribution isolation — the removal manifest

The suite is a **contiguous, physically-removable set**. To exclude it from a
consumer distribution, remove exactly:

| Path | Role |
|---|---|
| `src/trusted_data_agent/core/platform_ontology/` | ontology projection (build-on-read) |
| `src/trusted_data_agent/core/trust/` | trust engine (this package) |
| `src/trusted_data_agent/api/platform_ontology_routes.py` | ontology REST blueprint |
| `src/trusted_data_agent/api/trust_routes.py` | trust REST blueprint |
| `src/trusted_data_agent/api/evidence_routes.py` | Evidence REST blueprint (PROP-009; registered only beside the trust blueprint) |
| `static/js/handlers/evidenceView.js` | Evidence UI (the answer's evidence tree) |
| `static/js/evidenceVerify.js` | Browser evidence verifier (WebCrypto; the twin of `scripts/verify_evidence.py`) |
| `static/trust/verify-evidence.html`, `scripts/build_evidence_verifier_page.py` | Offline evidence verifier page + its builder |
| `src/trusted_data_agent/core/trust/ledger.py` | The hash-chained evidence ledger (its table, `trust_ledger`, lives in the auth db; removing the suite leaves it unread) |
| `src/trusted_data_agent/core/trust/anchoring.py` | External anchoring of ledger checkpoints (opt-in; table `trust_ledger_anchors`). `core/signing.py` — the signer seam — is NOT in the suite: provenance signing depends on it |
| `scripts/verify_evidence.py` | Standalone bundle verifier (needs only `cryptography`); `core/trust/evidence_bundle.py` builds the bundles |
| `static/js/handlers/ontologyPanelHandler.js` | ontology UI (nav view) |
| `static/js/handlers/trustBadge.js` | trust UI (badge + sign-off) |
| `templates/partials/ontology_nav.html` | sidebar nav item |
| `templates/partials/ontology_view.html` | the ontology view markup |
| `test/test_platform_ontology.py`, `test/test_trust_membrane.py`, `test/test_trust_attestation_history.py`, `test/test_evidence_bom.py`, `test/test_evidence_view.py`, `test/test_evidence_estate.py`, `test/test_evidence_bundle.py`, `test/test_evidence_ledger.py`, `test/test_evidence_custody.py` | tests |

**Always present (NOT part of the removable set):**
- `core/enterprise.py` — the lightweight gate (returns "not available" ⇒ nothing registers).
- `components/builtin/knowledge_graph/renderer.js` — the graph renderer is **shared** with the KG inspector; its ontology-mode code is guarded by `spec.is_platform_ontology` and is inert without the handler/projection.

**The platform runs cleanly with the whole set removed** — every seam is absence-tolerant:

| Seam | Behaviour when the suite is absent |
|---|---|
| `main.py` → `_register_optional_enterprise_suite(app)` | optional imports fail → neither blueprint registers; `APP_STATE['ontology_enabled']` / `['trust_membrane_available']` = `False`; logged as info |
| `api/routes.py` (index render) | passes both flags `False` to the template |
| `templates/index.html` | `{% if ontology_enabled %}{% include 'partials/…' ignore missing %}{% endif %}` → nothing rendered, and the missing partial is a **no-op** (`ignore missing`) |
| `static/js/ui.js` | `import('./handlers/ontologyPanelHandler.js').catch(...)` tolerates the file's absence |
| `static/js/handlers/ontologyPanelHandler.js` | every trust surface guarded by `if (window.TrustBadge)` |

Grep to confirm no stray references before a build:

```bash
grep -rn "platform_ontology\|core/trust\|trust_routes\|ontologyPanelHandler\|trustBadge\|window.TrustBadge\|ontology_enabled\|trust_membrane_available" \
  src/ static/ templates/ --exclude-dir=trust --exclude-dir=platform_ontology \
  | grep -v "enterprise.py\|_register_optional_enterprise_suite\|routes.py\|index.html\|ui.js\|knowledge_graph/renderer.js"
```

---

## Runtime gating — later, one edit

Enterprise-only is exactly this in [`core/enterprise.py`](../enterprise.py):

```python
tier = (APP_STATE.get("license_info") or {}).get("tier")
return tier == "Enterprise"
```

Everything (nav, both REST blueprints, the trust script) consults that one gate
(the Trust Membrane's `availability.py` defers to it). The gate is never scattered.
