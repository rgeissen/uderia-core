# Open Standards Architecture

**What this is:** a single reference for every **open, vendor-neutral standard** Uderia implements — so an external reviewer (or the *Knowledge Platform Comparison* assessment) can see, in one place, that Uderia is interoperable by construction rather than locked to a proprietary stack.

**Why it matters strategically:** Uderia's leapfrog thesis is to be GREEN on the *grounded-agentic + translation + policy* overlay **across any backend**. Open standards are what make "across any backend" literally true — they are the interop surface that lets Uderia's semantics, agents, identity, and telemetry leave the platform and be consumed by tools that have never heard of Uderia. This doc maps directly to assessment requirement **T4** (open / interoperable semantic standards — *all six vendors AMBER, Uderia GREEN*) and materially supports **A3** (MCP), **A6** (eval/observability), and **P3/P5** (identity / compliance).

**Last updated:** 2026-10-30

> Honesty is a design rule here (engineering charter #19): each entry states GA vs partial, and where a standard is *shaped-like* rather than *wire-conformant* it says so. Nothing below is aspirational — every row is anchored to real code.

---

## 1. Posture at a glance

| Domain | Standard | Status | Where |
|---|---|---|---|
| **Semantic interchange** | SKOS · RDFS/OWL · RDF Data Cube (QB) · PROV-O · DCAT | **GA** (Tier 4.1) | `semantic_interchange/` |
| | RDF serializations: Turtle · JSON-LD · RDF/XML · N-Triples | **GA** | `semantic_interchange/serializers.py` |
| | Reading a declared standard (OWL/RDFS classes, `rdfs:domain`, `owl:Restriction`, SHACL `sh:property`) | **GA** (narrow: class definitions only) | `semantic_interchange/ontology_classes.py`, `standards.py` |
| **Agent interoperability** | Model Context Protocol — **client** | **GA** (resource *listing* only) | `mcp_adapter/adapter.py`, `core/platform_connector_registry.py` (`mcp_stdio` / `mcp_http` connectors) |
| | Model Context Protocol — **server** | **GA** (Tier 2.1) | `mcp_server/` |
| **Observability** | OpenTelemetry / OTLP — **span shape** | **Partial** (OTLP-*shaped* dict + pluggable exporter; not OTLP-over-gRPC/HTTP) | `eval/online.py` |
| **Identity & auth** | OAuth 2.0 (Google, GitHub) | **GA** | `auth/oauth_handlers.py` |
| | OpenID Connect (generic IdP) | **GA** | `auth/oidc_provider.py`, `schema/30_sso.sql` |
| | SAML 2.0 (HTTP-Redirect/POST, XML-DSig) | **GA** | `auth/saml_provider.py`, `schema/31_saml.sql` |
| | JWT (RFC 7519) | **GA** | `auth/security.py`, `auth/middleware.py` |
| **Cryptography & provenance** | Ed25519 (EdDSA) signed provenance + attestations | **GA** | `core/provenance.py`, `eval/online.py` |
| | Fernet (AES-128-CBC + HMAC-SHA256) credential encryption | **GA** | `core/platform_connector_registry.py`, `auth/encryption.py` |
| **Retrieval & scheduling** | BM25 (sparse / hybrid search) | **GA** (Teradata backend) | `api/knowledge_routes.py` |
| | POSIX cron (5-field) | **GA** | `core/task_scheduler.py` |
| **API** | REST/HTTP over ASGI (Quart) | **GA** (RESTful; no generated OpenAPI spec) | `api/rest_routes.py` |

---

## 2. Semantic interchange — the centerpiece (T4)

The KG is a navigable **ontology** and metrics are a real **semantic layer**. Tier 4.1 makes both consumable by the semantic-web ecosystem through a **pure, deterministic, zero-LLM, bidirectional** adapter. Full design: `TIER_4.1_OPEN_STANDARDS.md`; proof: [proof-packs/T4_OPEN_STANDARDS.md](../proof-packs/T4_OPEN_STANDARDS.md).

| Uderia concept | Standard | Mapping |
|---|---|---|
| Concept / taxonomy (`business_concept`, `taxonomy`, `domain`) | **SKOS** | `skos:Concept` in a `skos:ConceptScheme`; `contains`→`skos:narrower`, `is_a`→`skos:broader` |
| Logical ontology (`entity`, `attribute`, `foreign_key`) | **RDFS / OWL** | `owl:Class` / `owl:DatatypeProperty` (+`rdfs:domain`) / `owl:ObjectProperty` |
| Metrics (dimensions + measures) | **RDF Data Cube (QB)** | `qb:DataStructureDefinition` + `qb:DimensionProperty` / `qb:MeasureProperty` components |
| Lineage (`implements`, `derives_from`) | **PROV-O** | `prov:wasDerivedFrom` (physical ← logical; view ← metric) |
| Catalog (KG + each metric) | **DCAT** | `dcat:Dataset` / `dcat:DataService` |

**The dual-layer design (the key idea).** Every export carries two parallel layers: the **standard predicates** (SKOS/OWL/QB/PROV/DCAT) for *interoperability* — a Protégé / SPARQL / DCAT consumer reads only these and never touches Uderia internals — and a **`uderia:` annotation layer** (entity type, the full properties dict as a JSON literal, reified relationships with type/cardinality/metadata) for *lossless reversibility*. Standard predicates are used **only where the semantics are exact**; types without an exact standard predicate fall back to a `uderia:` predicate rather than misusing a standard one (charter #1, no shortcuts).

**Bidirectional + deterministic.** Import reads the `uderia:` layer straight back (exact). A *foreign* RDF graph with no `uderia:` layer degrades to a best-effort standard-predicate mapping that records what it could not place in `warnings` (honest, never silent). Both directions are 100% deterministic — there is no LLM in the path (charter #20).

**Key files:** `semantic_interchange/` — `vocab.py` (namespaces + IRI minting + type maps), `kg_to_rdf.py:build_kg_graph()`, `rdf_to_kg.py:parse_graph()`, `metric_to_qb.py` (QB + PROV), `catalog.py` (DCAT), `serializers.py` (Turtle/JSON-LD/RDF-XML/N-Triples via `rdflib>=7`). REST: `GET …/export?format=skos|owl|rdf&serialization=…`, `GET …/metrics/<name>/export`, `POST …/import-rdf` (`api/rest_routes.py`).

**Proof:** a live 86-entity / 128-relationship export→reimport with **0 loss** + a standalone offline verifier (`docs/proof-packs/samples/verify_open_standards.py`, `pip install rdflib`, 23/23: round-trip · QB structural conformance · third-party SPARQL consumption · tamper) + 80 CI assertions (`test/test_semantic_interchange.py`).

### 2a. Reading a declared standard (the inbound direction)

A conformant data product names the standard it implements (`industry_standard` on an entity, and where the producer went further an `ontology_iri` naming the exact class). The platform follows that reference instead of storing it as an inert string:

- **Resolve and fetch** — `semantic_interchange/standards.py` (`expand_iri`, `namespace_of`, `sources_for`, `fetch_ontology`). The primary path needs no configuration: an ontology namespace is dereferenceable by convention (ask for `text/turtle` / RDF/XML / JSON-LD; a namespace that answers `text/html` is documentation and is skipped, never mis-parsed). `NAMESPACE_SOURCES` is the fallback for standards that serve only documentation at their namespace (seeded: UCO and CASE) — adding one is a row, not a code path.
- **Read the classes** — `semantic_interchange/ontology_classes.py`: per `owl:Class` / `rdfs:Class`, the label (`rdfs:label`/`skos:prefLabel`), definition (`skos:definition` before `rdfs:comment`), superclasses (`rdfs:subClassOf`) and the properties the standard says the class carries — via `rdfs:domain`, an `owl:Restriction`, or **SHACL** (`sh:property` → `sh:path`, incl. `sh:targetClass` shapes), with required flags. Deliberately narrow: not general ontology import.
- **Align** — `agent/kg_constructors/ontology_alignment.py` (`align`, used by the ANDP constructor `andp_semantic.py`) turns each ingested class into a `business_concept` node and an `is_a` edge from the product entity (and between classes when both were ingested); it never invents a class the fetched ontology does not define.

Gates: `test/test_ontology_classes.py`, `test/test_ontology_standards.py`, `test/test_andp_semantic_walker.py`.

---

## 3. Agent interoperability — Model Context Protocol

Uderia speaks MCP **both ways**, which is the bidirectional ecosystem story: it consumes external tools *and* exposes its own governed agents as tools.

**MCP client** (`mcp_adapter/adapter.py`) — connects to external MCP servers and discovers their tools / prompts / resources (`load_and_categorize_mcp_resources()`; `list_tools`/`list_prompts`/`list_resources`) and invokes them (`invoke_mcp_tool()`). Transports: **STDIO** (subprocess) and **Streamable-HTTP** (remote endpoints). Admin-governed platform connectors speak the same protocol (`connector_type` `mcp_stdio` / `mcp_http`, the latter via the `mcp` SDK's `streamablehttp_client`; [CONNECTOR_ARCHITECTURE.md](CONNECTOR_ARCHITECTURE.md)). *Partial:* resource **listing** is GA; resource **invocation** is not yet implemented (tools + prompts are GA).

**MCP server** (`mcp_server/`, Tier 2.1) — Uderia exposes itself as an MCP server via `fastmcp>=2.0.0` (`server.py` FastMCP app). Any MCP client (Claude Desktop, Cursor, another agent) can call a governed Uderia profile *as a tool*: `ask_profile()` runs the full grounded + guardrailed pipeline, plus `list_profiles()` / `list_knowledge_graphs()`, the signed data-product and action faces (`list_data_products()` / `query_data_product()` — OBDA-READ; `list_actions()` / `perform_action()` — OBDA-ACT) and tier-gated read tools (`register_tools()` gates the surface by profile/tier rank). Transports: **STDIO** (default) and **streamable-HTTP** (`__main__.py` CLI). Auth = a 90-day Uderia access token; tool surface is hard-gated by consumer tier. Detail: [MCP_SERVER_ARCHITECTURE.md](MCP_SERVER_ARCHITECTURE.md).

---

## 4. Observability — OpenTelemetry / OTLP

The Agent Ops & Evaluation layer (WS3) emits **OTLP-shaped quality spans** for every scored turn / flow run. `eval/online.py:build_quality_span()` produces a vendor-neutral span dict with OTLP-style attributes (`eval.profile_class`, `agent.profile_tag`, `agent.profile_id`, `eval.cost_usd`, `eval.judged`, …), routed through a pluggable `OnlineEvalExporter` ABC with a default `JsonlExporter` (durable NDJSON sink) and a `MemoryExporter` for tests.

**Honest scope (charter #19):** this is an OTLP-*shaped* span **dict** behind a swappable exporter seam — it is **not** OTLP-over-gRPC/HTTP to a collector out of the box. The shape and the exporter abstraction make wiring a real OTLP exporter a drop-in; the wire-protocol exporter itself is not claimed as shipped. Detail: [AGENT_OPS_EVAL_ARCHITECTURE.md](AGENT_OPS_EVAL_ARCHITECTURE.md).

---

## 5. Identity & authentication

Uderia integrates with enterprise IdPs through the established open auth standards — JIT-provisioning users and mapping IdP group membership to Uderia tiers on each login.

- **OAuth 2.0** (`auth/oauth_handlers.py`) — Google and GitHub; authorization-code exchange (`exchange_code_for_token()`) + user-info fetch. **GA.**
- **OpenID Connect** (`auth/oidc_provider.py`, `schema/30_sso.sql` `sso_configurations`) — generic OIDC against any compliant IdP (Okta, Azure AD, Google Workspace, Keycloak, Auth0, Ping): discovery via `/.well-known/openid-configuration`, authorization URL, code→token, `id_token` validation (iss/aud/exp/nonce), `group_tier_map` for JIT tier assignment. **GA.**
- **SAML 2.0** (`auth/saml_provider.py`, `schema/31_saml.sql` `saml_configurations`) — `AuthnRequest` over HTTP-Redirect, ACS over HTTP-POST, **XML-DSig** assertion-signature verification (`_verify_saml_signature()`), on-demand SP metadata. Works with ADFS, Okta, Azure AD, Shibboleth, Ping, OneLogin. **GA.**
- **JWT — RFC 7519** (`auth/security.py` `generate_auth_token()` / `verify_auth_token()`, enforced in `auth/middleware.py`) — signed web-session tokens (`jti`, `exp`, `iat`, revocation lookup) plus long-lived REST access tokens. **GA.**

Governance + JIT detail: [SECURITY_ARCHITECTURE.md](SECURITY_ARCHITECTURE.md).

---

## 6. Cryptography & provenance

- **Ed25519 (EdDSA)** — the Execution Provenance Chain and eval attestations are signed with Ed25519 (`core/provenance.py:_get_provenance_signing_key()`; key auto-generated at `tda_keys/provenance_key.{pem,pub}`). `eval/online.py:sign_attestation()` binds a verdict digest to the turn's provenance chain-tip and signs it, so an eval verdict is cryptographically bound to the trajectory it grades. A **standalone offline verifier** ships in the A6 proof-pack — anyone validates a real signed span with `pip install cryptography` and no Uderia install. **GA.** Detail: [AGENT_OPS_EVAL_ARCHITECTURE.md](AGENT_OPS_EVAL_ARCHITECTURE.md), [proof-packs/A6_EVAL_OBSERVABILITY.md](../proof-packs/A6_EVAL_OBSERVABILITY.md).
- **Fernet (AES-128-CBC + HMAC-SHA256)** — all stored secrets (LLM keys, OAuth/OIDC client secrets, SAML SP private key, connector credentials) are Fernet-encrypted with PBKDF2-HMAC-SHA256-derived keys (`core/platform_connector_registry.py:_platform_fernet()`, `auth/encryption.py`). **GA.**

---

## 7. Retrieval, scheduling & API

- **BM25** (`api/knowledge_routes.py:enable_teradata_bm25()`) — sparse-lexical hybrid search (RRF / weighted-sum / weighted-RRF fusion with a tunable sparse weight) on the **Teradata** vector backend. **GA** (Teradata only). Detail: [VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md](VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md).
- **POSIX cron** (`core/task_scheduler.py`) — standard 5-field cron (`"0 9 * * 1-5"`) plus interval shorthand, executed via APScheduler. **GA.** Detail: [SCHEDULER_ARCHITECTURE.md](SCHEDULER_ARCHITECTURE.md).
- **REST/HTTP** (`api/rest_routes.py` + others) — a full RESTful surface over Quart (async ASGI), JWT/access-token authed. **GA** as RESTful HTTP; *not claiming* a generated OpenAPI/Swagger spec (the API is documented in [restAPI.md](../RestAPI/restAPI.md), not machine-emitted).

---

## 8. Mapping to the assessment

| Assessment req | Standards that satisfy it | Status |
|---|---|---|
| **T4** open / interoperable semantic standards | SKOS · OWL/RDFS · RDF Data Cube · PROV-O · DCAT (+ 4 RDF serializations), bidirectional & lossless | **GREEN** (all six vendors AMBER) |
| **A3** MCP | MCP client **and** server | **GREEN** |
| **A6** eval / observability | OTLP-shaped spans + Ed25519-signed attestations | **G-credible** |
| **P3 / P5** identity / compliance enablement | OAuth 2.0 · OIDC · SAML 2.0 · JWT | identity GA; certs are a deployment track, not code |

---

## 9. What we deliberately do **not** claim (charter #19)

- **OTLP is a span *shape* + exporter seam**, not OTLP-over-gRPC/HTTP to a collector. Wiring a real OTLP exporter is a drop-in; it is not shipped.
- **MCP client resource *invocation*** is not implemented (listing is). Tools + prompts are GA both ways.
- **No machine-generated OpenAPI spec** — the REST API is RESTful and hand-documented, not emitted as a formal OpenAPI artifact.
- **Foreign-ontology import is best-effort**, not lossless — only Uderia's own RDF (with the `uderia:` layer) round-trips exactly; a foreign graph is mapped heuristically with honest `warnings`.
- **QB conformance is structural** (a DataStructureDefinition), not observation-level — the W3C QB IC-1..21 integrity constraints concern `qb:Observation`/`qb:DataSet` instance data, which the export does not emit, so they are N/A and not claimed.

---

## 10. Related documents

- `TIER_4.1_OPEN_STANDARDS.md` — semantic-interchange design + architecture
- `TIER_4.3_ONTOLOGY_PRODUCT.md` — ontology as a governed product (T2; the governed ontology model + conformance gate, reuses 4.1's export)
- [proof-packs/T4_OPEN_STANDARDS.md](../proof-packs/T4_OPEN_STANDARDS.md) — the T4 proof + offline verifier
- [MCP_SERVER_ARCHITECTURE.md](MCP_SERVER_ARCHITECTURE.md) · [CONNECTOR_ARCHITECTURE.md](CONNECTOR_ARCHITECTURE.md) — MCP server / client + connectors
- [AGENT_OPS_EVAL_ARCHITECTURE.md](AGENT_OPS_EVAL_ARCHITECTURE.md) — OTLP-shaped spans + Ed25519 attestations
- [SECURITY_ARCHITECTURE.md](SECURITY_ARCHITECTURE.md) — OAuth / OIDC / SAML / JWT, JIT provisioning
- [ONTOLOGY_ARCHITECTURE.md](ONTOLOGY_ARCHITECTURE.md) — the KG/ontology the semantic export draws from
