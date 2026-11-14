# T2 — Ontology as a Governed Product

**What this proves:** *ontology / business-concept modeling* as a governed product. The capability is described elsewhere (the KG **is** a navigable ontology — [ONTOLOGY_ARCHITECTURE.md](../Architecture/ONTOLOGY_ARCHITECTURE.md)); this pack proves the missing half: it is a **governed, versioned, standards-published product**.
**Design:** `TIER_4.3_ONTOLOGY_PRODUCT.md`.
**Principles:** a capability is claimed only with its proof · *deterministic where possible* (conformance and the state machine use no LLM) · *limits stated honestly*.

---

## The capability

A thin governance + conformance layer over the existing KG version, marketplace, and Tier-4.1 export machinery turns an ontology into a product with a **lifecycle**, a **governance gate**, and a **quality definition**:

- **Lifecycle** — `draft → published → deprecated`, pinning an **immutable published snapshot** (not "whatever the graph is now").
- **Conformance gate (the central gate)** — a deterministic validator (`kgstore/conformance.py`) checks the KG against the canonical ontology topology (layer tagging, logical backbone, physical→logical `implements`, column containment, orphans, domain coherence, dangling edges) → `conformant` / `partial` / `nonconformant` with **typed, severity-banded issues**. Publishing a non-conformant ontology is **blocked** by default (admin policy + per-publish override); a partial one publishes with an honest **badge**.
- **Standards product face** — `GET /v1/ontologies/<id>` serves the published snapshot **content-negotiated** to OWL/SKOS/Turtle/JSON-LD (via the Tier-4.1 converter), `?version=` pinned; `GET /v1/ontologies` is the catalog. *"Define once, consume everywhere (BI and AI)"* made literal.
- **Provenance** — a *publish* emits an **Ed25519 signed attestation** binding the conformance verdict + the pinned version (reusing the eval substrate), so *"published as conformant v1.2.0"* is cryptographically provable.

Default-off governance reproduces today's behaviour byte-for-byte.

---

## The proof

### 1. Reproducible end-to-end verifier — `scripts/verify_ontology_governance.sh`

Drives the full governed lifecycle through the REST API against a running Uderia and asserts every step, then restores the KG to pristine (the graph content is never touched):

```bash
BASE=http://localhost:5050 ./scripts/verify_ontology_governance.sh
```

Asserts: list-enrichment carries lifecycle status · conformance runs deterministically · `draft` initial · **publish → published + version pinned + signed Ed25519 attestation** · appears in the catalog · **content-negotiated Turtle (200) + native-JSON** serving of the published snapshot · deprecate → drops from catalog · audit trail recorded. **All assertions pass against a live server** (live-verified 2026-06-29 on a real 86-entity KG: conformance `partial` → published with `partial_conformance` badge, attestation `signed:true` with a real key fingerprint).

### 2. The conformance gate, demonstrated

A non-conformant ontology is **refused** publication with its typed issues; a partial one publishes with the badge; a conformant one publishes clean. This is the deterministic, ontology-grounded quality definition — *a graph cannot be a governed ontology product unless it is a valid ontology model.*

### 3. CI-pure unit proof — 70 assertions

- [`test/test_kg_conformance.py`](../../test/test_kg_conformance.py) (**40**) — the validator against fixtures, every violation class, severity→status mapping, determinism, and a **drift guard** asserting the conformance type sets match the canonical `graph_store` enums.
- [`test/test_governance_lifecycle.py`](../../test/test_governance_lifecycle.py) (**30**) — every legal/illegal transition, the approval gate (separation of duties), the conformance gate (block/partial/override), default-policy direct publish.

### 4. Signed publish attestation

The publish attestation reuses the same Ed25519 provenance key as the A6 eval attestations (`eval/online.py:sign_payload`), so it is verifiable offline with the same key + the [A6 verifier](A6_EVAL_OBSERVABILITY.md) pattern — payload `kg_id | published_version_id | conformance | semantic_version | publish`.

---

## Honest limits (charter #19)

- **Release has no runtime effect on the agents (Model A).** Lifecycle "publish" is a *release* act — **not** marketplace publishing, and **not** a switch that changes what the IFOC engines ground on (they use the live active KG, not the released snapshot). So the conformance gate protects *external consumers*, not the agents. Making release gate agent grounding is **Model B** — **built Sep 2026 as a per-agent opt-in** (`componentConfig.knowledge_graph.groundOn: "released"`; an opted-in agent reads the released view for every KG read, writes are refused, trust hashes the release). The default stays Model A, so this pack's evidence is unchanged for any agent that has not opted in. **For a solo user grounding only their own agents, the standalone-valuable artifact is the conformance check (an ontology linter); the release/publish machinery is external-consumption / compliance / multi-team facing until Model B.** See `TIER_4.3 §1a`.
- **"Publish" is overloaded** with marketplace publish — *Release* is the clearer verb (rename recommended).
- **Conformance is structural, not semantic** — it validates context-topology (layers, `implements`, domains, dangling edges), not whether the business model is *correct*. A conformant ontology can still model the wrong domain.
- **Approval is lightweight** — a single approval gate with separation of duties, not a multi-reviewer workflow. In a single-admin instance the owner cannot self-approve (by design), so approval-required mode needs a distinct approver.
- **The shared lifecycle primitive is built KG-first** — applying it to agent packs (Tier 3.2 / P7) is a separate epic.

---

## Code

- Pure: `kgstore/conformance.py`, `core/governance_lifecycle.py`
- Glue: `core/ontology_governance.py`, `graph_store.py` (lifecycle columns + accessors + audit), `kgstore/settings.py` (policy), `eval/online.py` (`sign_payload` + span)
- REST: `api/rest_routes.py` (`…/conformance`, `…/lifecycle[/<action>]`, `/v1/ontologies[/<id>]`)
- UX: `static/js/handlers/knowledgeGraphPanelHandler.js` (badges + governance dialog), `static/js/adminManager.js` + `templates/index.html` (admin policy)
- Proof: `scripts/verify_ontology_governance.sh`, `test/test_kg_conformance.py`, `test/test_governance_lifecycle.py`
