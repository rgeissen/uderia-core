# 🔐 Security Architecture

Uderia's security model is organized in **two complementary pillars**:

- **🛡️ Deployment Security** — classic information security: protecting identity, data, secrets, and intellectual property, and proving the integrity of everything the system does. Maps to ISO 27001 / SOC 2 / FedRAMP.
- **🔬 Agent Trust & Assurance** — AI governance: proving the *AI's output* is truthful, safe, in-scope, and verifiably good. Maps to the EU AI Act / NIST AI RMF.

The pillars meet at one cryptographic substrate — the **same Ed25519 key that signs each execution trajectory also signs the evaluation verdict that grades it** — so deployment integrity and answer trustworthiness are provable with one chain of custody.

**Pillar 1 — Deployment Security**

| System | What it protects |
|---|---|
| **System Prompts and the Licence** | Versioned plain-text prompts, releases that never overwrite a person's edit, the signed licence that activates the enterprise package |
| **Execution Provenance Chain (EPC)** | Integrity of every LLM decision, tool call, and response |
| **Authentication & Identity Federation** | User identity via passwords, OAuth 2.0, OIDC, and SAML 2.0 SSO |
| **Secrets & Credential Management** | LLM API keys, SSO secrets, and connector credentials |
| **Access Control & Consumption Governance** | Who can do what, and how much |

**Pillar 2 — Agent Trust & Assurance**

| System | What it protects |
|---|---|
| **Answer Gates (grounding · honesty · guardrails · leaked-SQL recovery · turn failure)** | Truthfulness and safety of every answer *before* it reaches the user — the deterministic gates between the model's draft and the user ([Architecture →](../Architecture/ANSWER_GATES_ARCHITECTURE.md)) |
| **Agent Authority** | What an agent may do: a scoped principal, fail-closed at every tool chokepoint ([Architecture →](../Architecture/AGENT_AUTHORITY_ARCHITECTURE.md)) |
| **Trust Membrane & Evidence** | Signed agent identity, sign-off/attest, and a per-answer bill of materials; the acts of signing (sign-off, attestation, revocation) are the enterprise package's, the core shows trust and verifies signatures ([Trust Membrane →](../Architecture/TRUST_MEMBRANE_ARCHITECTURE.md) · [Evidence →](../Architecture/EVIDENCE_ARCHITECTURE.md)) |
| **Agent Evaluation & Attestation** | Continuous, tamper-evident proof that every answer was scored for correctness, cost, and safety — verdict cryptographically bound to the trajectory it grades (observe + prove) |

Together these establish a **zero-trust execution model**: the intelligence that drives the AI is cryptographically protected, every action the AI takes is cryptographically recorded, every answer is checked for grounding and safety before it leaves the building, and every answer is independently scored with the verdict cryptographically bound to the trajectory it grades — positioning Uderia for enterprise compliance requirements including SOX, GDPR, EU AI Act, ISO 27001, HIPAA, and FedRAMP.

---

## Pillar 1 — 🛡️ Deployment Security

*Information security: identity, data, secrets, IP, and the integrity of every action. (Detailed doc: [SECURITY_ARCHITECTURE.md](../Architecture/SECURITY_ARCHITECTURE.md).)*

## 🛡️ System Prompts: Plain, Versioned, Never Overwritten by a Release

The system prompts — the planner's, the tactical executor's, the self-correction, classification, conversation and coordinator prompts — are **plain text files shipped with the code** (`schema/default_prompts/`). What protects them is not a cipher but the record:

- **One source**: fifteen tracked files; a release carries its prompts, and a running deployment picks them up on its next start.
- **Every change versioned**: the history (`prompt_versions`) keeps each replaced text with who changed it and why.
- **A person's edit is final**: a prompt someone edited is never overwritten by a release; the editor's *Reset to default* hands it back.
- **Overrides are a layer**: per-user and per-profile overrides sit above the base prompt and are never touched by a release.
- **The editor is enterprise**: editing in the interface is part of the enterprise package, activated by a signed Enterprise or Partner licence (RSA-PSS, 4096-bit); the public core runs the prompts without it.

[Architecture →](../Architecture/PROMPT_ARCHITECTURE.md)

## 🔗 Execution Provenance Chain (EPC)

The EPC is implemented as a blockchain-like hash chain signed with Ed25519.

**How it works:**

1. Each execution step (query intake, strategic plan, tool call, tool result, synthesis, etc.) is recorded as a **provenance step**
2. Each step's content is SHA-256 hashed (content is hashed, never stored — no sensitive data in the provenance record)
3. Steps are **hash-chained**: each step's chain hash incorporates its index, type, content hash, and the previous step's chain hash
4. Every chain hash is **signed with Ed25519**, enabling offline verification with just the public key
5. Across turns, the first step of each turn links to the last step of the previous turn, creating **full session integrity**

```
Turn 1: [query] -> [plan] -> [tool_call] -> [tool_result] -> [complete]
           h0   ->   h1   ->     h2      ->      h3       ->     h4
                                                                   |
Turn 2: [query] -> [llm_call] -> [response] -> [complete]         |
           h5   ->    h6      ->     h7      ->    h8              |
           ^                                                       |
           previous_turn_tip = h4  (cross-turn cryptographic link) +
```

**24 step types** across all five profile classes ensure comprehensive coverage:

| Profile | Example Steps Recorded |
|---|---|
| **Optimize** (tool_enabled) | Strategic plan, plan rewrites, tactical decisions, tool calls, tool results, self-corrections |
| **Ideate** (llm_only) | Knowledge retrieval, LLM calls, LLM responses |
| **Focus** (rag_focused) | RAG search, RAG results with doc IDs and scores, synthesis |
| **Ideate + MCP** (conversation_with_tools) | Agent tool calls, agent tool results, agent LLM steps |
| **Coordinate** (genie) | Child profile dispatch, cross-session chain references, coordinator synthesis |

**Three verification levels:**

1. **Chain Integrity** (offline-capable) — verifies hash linking, hash computation, and Ed25519 signatures using only the public key
2. **Content Verification** — confirms content hashes match actual session data
3. **Session Integrity** — verifies cross-turn linking across all turns, including genie's cross-session Merkle-tree structure

**REST API for auditors:**

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/sessions/{id}/provenance` | Full provenance for all turns |
| `POST /api/v1/sessions/{id}/provenance/verify` | Verify chain integrity |
| `GET /api/v1/sessions/{id}/provenance/export` | Download JSON for offline audit |
| `GET /api/v1/provenance/public-key` | Download public key for independent verification |

**Key properties:**
- **Zero new dependencies** — uses the `cryptography` library already present in the project
- **Negligible overhead** — ~100 microseconds per step (hashing + signing), invisible against LLM latency
- **Degraded mode** — if the signing key is unavailable, hashes are still recorded (unsigned); execution is never blocked
- **Key rotation** — `maintenance/rotate_provenance_key.py` generates new keys; old chains remain verifiable via stored key fingerprints
- **Backward compatible** — existing sessions without provenance data are handled gracefully
- **Key at rest** — set `TDA_PROVENANCE_KEY_PASSPHRASE` to encrypt the signing key file (migrated in place on first start, same public key)

[Architecture →](../Architecture/SECURITY_ARCHITECTURE.md) · [Evidence →](../Architecture/EVIDENCE_ARCHITECTURE.md)

## 🔑 Authentication & Identity

Six authentication methods with JIT user provisioning and enterprise identity federation:

| Method | Mechanism | Key Properties |
|---|---|---|
| **Password** | bcrypt (12 rounds), per-password salt | Strength validation; lockout after 5 failures; 15-min lock + exponential delay (2s → 4s → 8s → 16s → lock) (`TDA_MAX_LOGIN_ATTEMPTS`, `TDA_LOCKOUT_DURATION_MINUTES`) |
| **JWT Sessions** | HS256, 24-hour expiry (`TDA_JWT_EXPIRY_HOURS`) | In-memory LRU cache (10k entries, 60s TTL); per-token revocation; IP + user-agent recorded at issuance; idle limit `TDA_SESSION_IDLE_MINUTES` (off by default, 30 min in hardened mode) enforced in the browser and on the server — a revoked or idle token signs the tab out once |
| **Long-Lived Access Tokens** | `tda_` prefix + `secrets.token_urlsafe(24)` | SHA-256 hash stored (never plaintext); shown once at creation; per-use `last_used_at` / `use_count` / `last_ip_address` |
| **OAuth 2.0** | Google, GitHub, Microsoft, Discord, Okta | Authorization Code flow; no OAuth tokens stored; JIT provisioning; duplicate email detection |
| **OIDC Enterprise SSO** | Any OIDC IdP via `/.well-known/openid-configuration` | `python-jose` id_token validation (sig + iss + aud + exp + nonce); group-to-tier mapping; back-channel logout |
| **SAML 2.0 Enterprise SSO** | ADFS, Okta, Azure AD, OneLogin, PingIdentity | SP metadata XML endpoint; `signxml` assertion signature verification; group-to-tier re-evaluated on every login |

**Group-to-tier resolution (SSO):** Highest-privilege group wins across all groups the user belongs to — re-evaluated on every login so IdP group changes take effect immediately with no manual intervention.

**Internal service tokens:** The Genie coordinator issues 30-minute internal JWT tokens (`"internal": true`, not stored in DB) for child session creation — preventing lateral privilege escalation across slave sessions.

[Architecture →](../Architecture/SECURITY_ARCHITECTURE.md) · [OAuth & SSO setup →](../OAuth/README.md)

---

## 🔒 Authorization & Access Control

**Three-tier RBAC** stored as `profile_tier` + `is_admin` on the `users` table:

| Tier | Capabilities |
|---|---|
| **User** | Execute queries, manage own profiles and collections, view own sessions |
| **Developer** | All User + create/modify profiles, configure data sources, access REST API |
| **Admin** | All Developer + user management, system configuration, platform connector governance, security settings, compliance reporting |

**Request-level enforcement** — three middleware policies guard every endpoint:
- **Required authentication** — validates the Bearer token (access token or JWT); rejects unauthenticated requests with 401
- **Admin-only** — requires authentication plus an admin check; returns 403 for non-admins
- **Optional authentication** — attempts authentication but lets the request through unauthenticated, for public endpoints that behave differently when signed in

**Consumption profiles & rate limiting:**
- Per-user token quotas: `prompts_per_hour`, `prompts_per_day`, `input_tokens_per_month`, `output_tokens_per_month` (NULL = unlimited)
- IP-based token-bucket rate limits (defaults): login 5/min · registration 3/hr · API 60/min; global-override user limits 100 prompts/hr · 1,000/day · 10 config changes/hr
- **Fail-open with a visible bypass** — if the quota check itself errors (e.g. database unavailable) the turn proceeds so users are not locked out; the turn logs a `[SECURITY]` warning, shows a Security Warning event in Live Status, increments a process-level bypass counter, and alerts administrators with a live banner and a CRITICAL log line (at most every 15 minutes)
- Consumption-profile quotas are enforced independently of the rate-limit switch

**REST activation gate** — an agent can be driven through the REST API, as the default agent or as a `profile_id` override, only once it has been **activated**, and activation requires every one of the agent's tests to pass (model credentials, data source). An agent that is not activated is refused up front with `403 profile_not_activated` and never reaches the engine; an unexpected error in the check denies too (fails closed). In-process orchestration — a coordinator driving its experts, the scheduler, a flow — does not pass through these entry points, so an expert's child session is created without the check while the coordinator itself must be activated.

[Architecture →](../Architecture/SECURITY_ARCHITECTURE.md) · [Consumption enforcement →](../Architecture/COST_TRACKING_ARCHITECTURE.md)

---

## 🗝️ Secrets & Credential Management

All secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before database storage — never stored in plaintext.

| Secret type | Key derivation | Salt |
|---|---|---|
| LLM API keys (per user) | PBKDF2-HMAC-SHA256, 100k iterations | User UUID |
| Platform connector admin credentials | PBKDF2-HMAC-SHA256, 100k iterations | `b'platform_mcp_registry'` (**frozen** — do not change) |
| OIDC `client_secret` | Same as connector credentials | Frozen salt |
| SAML `sp_private_key` | Same as connector credentials | Frozen salt |
| Per-user OAuth tokens (connectors) | PBKDF2-HMAC-SHA256, 100k iterations | `b'platform_mcp_registry'` (frozen platform salt) |

**REDACTED sentinel:** All API responses replace sensitive fields with the literal string `"[REDACTED]"` — confirming a credential is configured without revealing its value. Sending `"[REDACTED]"` in a PUT request preserves the existing encrypted value and skips re-encryption.

**Key rotation:** `maintenance/rotate_encryption_key.py` re-encrypts every stored secret (per-user credentials, connector credentials, OAuth tokens, SSO/SAML secrets, global TTS) under a new key. Stop the server first; see [SECURITY_ARCHITECTURE.md §13.1](../Architecture/SECURITY_ARCHITECTURE.md).

---

## 📋 Audit Logging

| Audit trail | Storage | Contents |
|---|---|---|
| JWT sessions | `auth_tokens` table | Token hash (SHA-256), IP, user-agent, issuance time, revocation status |
| API access tokens | `access_tokens` table | `last_used_at`, `use_count`, `last_ip_address` — updated on every successful auth |
| Failed logins | `users` table fields | `failed_login_attempts`, `locked_until`, `last_failed_login_at` |
| SSO tier/group changes | `sso_sync_events` table | Old/new tier, old/new groups, sync type (`login`\|`manual`), config ID — supports compliance access reviews |
| General actions | `audit_logs` table | User, action, resource, status (`success`\|`failure`), IP, user-agent, JSON details |
| Ontology lifecycle | `kg_lifecycle_audit` table | Every draft/approve/publish/deprecate attempt on a knowledge graph |
| Agent-pack lifecycle | `pack_lifecycle_audit` table | Every pack lifecycle attempt, allowed or denied |
| Execution provenance | Per-session chain | Every LLM call, tool call, and response — Ed25519-signed, offline-verifiable (see EPC section) |

**Append-only by default.** Database triggers, installed at every start, refuse UPDATE and refuse DELETE of any audit row younger than `TDA_AUDIT_RETENTION_DAYS` (default and minimum 365) on `audit_logs`, `sso_sync_events`, `kg_lifecycle_audit` and `pack_lifecycle_audit`. `audit_logs` stores each event's structured detail in `metadata_json`. Every audit event can also be shipped off-host to syslog (`TDA_AUDIT_SYSLOG`) or a JSON-lines file (`TDA_AUDIT_JSONL`). [Architecture →](../Architecture/SECURITY_ARCHITECTURE.md)

---

## 🌐 Network Security

- **TLS termination** via reverse proxy (nginx, Caddy, Traefik) — not built into the application server. TLS 1.2 minimum, TLS 1.3 recommended; HSTS at proxy level.
- **IP extraction priority:** `X-Forwarded-For` → `X-Real-IP` → `remote_addr`. Restrict which upstream IPs can set `X-Forwarded-For` at the proxy to prevent client IP spoofing attacks against rate limiting.
- **Rate limiting** is enforced server-side — not bypassable by client header manipulation.
- **CORS** allows any origin by default (the API authenticates with a bearer token, not a cookie); set `CORS_ALLOWED_ORIGINS` to a comma-separated list of exact origins to restrict it.

---

## 🐚 Platform Connector Security

Platform connectors (browser, files, web search, shell, email, calendar) operate under a strict three-layer governance model:

```
Admin Layer   →  installs connectors, sets available_tools, opt-in policy, admin credentials
     ↓
User Layer    →  toggles connectors on/off per profile (within admin bounds)
     ↓
Profile Layer →  selects active tools per connector (within admin-permitted set)
```

**Shell connector (`uderia-shell`)** requires two mandatory, independent security layers:
1. **Admin governance** — only explicitly enabled profiles can invoke shell tools; admin must acknowledge a security disclaimer before the enable toggle appears
2. **Docker isolation** — every invocation runs in a fresh throwaway container: no host filesystem access (only admin-configured mount paths), no network, 1 CPU / 512 MB RAM / 30-second timeout, read-only root filesystem with a 50 MB writable `/tmp`, user `nobody`, commands limited to the admin's allowlist; container destroyed immediately on completion

Every connector tool execution, shell commands included, is recorded in the audit log: who ran it, the connector and tool, the arguments (credential-like values redacted, long values truncated), the outcome and the duration. The output also returns to the calling turn, whose tool call is recorded in the session and its provenance chain.

**Namespace separation:** Platform connectors live in the `platform_connectors` table, configured by admins — permanently separate from user-configured MCP data source servers, never sharing a UI surface.

[Architecture →](../Architecture/CONNECTOR_ARCHITECTURE.md)

---

## Pillar 2 — 🔬 Agent Trust & Assurance

*AI governance: proving the AI's output is truthful, safe, in-scope, and verifiably good — the **observe → enforce → prove** trust story. (Detailed docs: [Answer Gates](../Architecture/ANSWER_GATES_ARCHITECTURE.md) · [Agent Authority](../Architecture/AGENT_AUTHORITY_ARCHITECTURE.md) · [Trust Membrane](../Architecture/TRUST_MEMBRANE_ARCHITECTURE.md) · [Evidence](../Architecture/EVIDENCE_ARCHITECTURE.md) · [Assurance Instruments](../Architecture/ASSURANCE_INSTRUMENTS_ARCHITECTURE.md) · [Agent Ops & Eval](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md). Operators: [runbook](../manuals/AGENT_TRUST_AND_ASSURANCE.md) · [Trust UX FAQ](../manuals/TRUST_UX_FAQ.md) · developers: [Building Assurance Modules](../manuals/BUILDING_ASSURANCE_MODULES.md).)*

## 🚦 Answer Gates — Between the Draft and the User

Between a model's draft and the user stand deterministic gates that run on every engine. The **grounding gate** checks the answer against the data and knowledge the turn actually had, and only a judge's ruling withholds; the **honesty gate** catches answers that misrepresent their own evidence ("none exist" after failed queries, a record no query produced); **guardrails** screen injection inbound and PII, toxicity and exfiltration URLs outbound. Every gate defaults to `observe`, fails open, stamps the turn for the Assurance dial, and under `review` parks the withheld original in the Assurance Inbox. The sections below take each gate in turn.

[Architecture →](../Architecture/ANSWER_GATES_ARCHITECTURE.md)

## 🎯 Grounding Gate (Runtime Anti-Hallucination)

Handing a model relevant context does not make it use that context. Uderia's grounding closes that loop with a **synchronous runtime gate** that, before an answer reaches the user, decides whether it stayed within the Knowledge Graph's declared **deployed scope**: **pass · annotate · block**.

The gate is built on a deliberate two-layer split between deterministic computation and LLM judgement:

- **Deterministic layer (0 tokens, exact, reproducible)** — scope derivation from the resolved KG, a recall-first trigger that flags any structural data reference (a dotted `db.table`, a quoted identifier, a technical identifier near a container word) that falls outside `scope_databases ∪ scope_entities ∪ system catalogs`. Self-maintaining, degrades to a database-only check when no entity scope is declared.
- **LLM layer (only when the deterministic trigger fires)** — a single faithfulness judge makes the call no algorithm can: **block** (the answer drifted onto sources it was never grounded in), **pass** (faithful, including honest "not in scope" refusals), or **annotate** (judge unavailable → fail-safe). Bounded by a timeout and fully attributed through the cost engine.

**Governance modes**, identical to the guardrail ladder: `off` · `observe` (measure + emit, never alter — the default, byte-identical to the ungated baseline) · `annotate` · `review` (withhold exactly like `enforce`, but park the original for a human to approve or uphold — nothing lost, nothing silently delivered) · `enforce`. A global admin master switch plus a per-profile mode give the standard global → per-profile governance chain. The gate runs across **all five engine classes** (Ideate, Focus, Optimize, Coordinate, Ideate+MCP), abstains cleanly when a turn has no KG scope, **fails open** on any internal error, emits a Live-Status event live and on reload, and stamps its verdict onto the persisted turn for audit.

**Proof, not just capability** — the published anti-hallucination benchmark (36 real turns, judge-labelled) measures the trigger's recall of judge-confirmed scope drift: **60% recall, 2 false negatives, net governance drift 14% → 6%** (rerun 2026-10-31). The rerun first measured 0% — a regression no test had caught (catalog listings excused the databases the agent then reported) — fixed and pinned in CI. The benchmark reports recall *and* precision honestly, publishes every miss by case id, and the gate names the out-of-scope source it withheld on ([the numbers](../../static/trust/benchmarks.html)).

On a knowledge (Focus) answer the same gate runs a corpus-support trigger — unsupported numbers, quotes and identifiers — and the Live Status panel names the finding it acted on and the modules it consulted.

> *The failure this exists to prevent: an agent that received a complete, scope-correct KG enrichment, ignored it, brute-forced 19 calls across unrelated databases, and produced a confident-but-wrong answer. The grounding gate is the enforcement that the substrate alone could not provide.*

[Architecture →](../Architecture/ANSWER_GATES_ARCHITECTURE.md) · Tier record → (`docs/roadmap/TIER_1.1_GROUNDING_GATE.md`)

## 🚧 AI Guardrails (PII, Toxicity, Prompt Injection)

A request-path enforcement primitive that guards both directions of the conversation, built on the same architecture as the grounding gate.

| Direction | What it catches | Enforce action |
|---|---|---|
| **Outbound** (answer → user) | PII (email, SSN, Luhn-validated card, phone, AWS key, API token, IBAN, IPv4), heuristic toxicity | PII **redacted** in place (`[REDACTED:email]`…); toxic output **blocked** |
| **Inbound** (user → model) | Prompt-injection / jailbreak attempts | Turn **hard-blocked** before any LLM call — the injected text never reaches the model |

- **Privacy invariant** — guardrail events carry category **labels only, never the raw PII value**.
- **Mode ladder** — `off` · `observe` · `annotate` · `review` (enforce with retention — park the original for human disposition) · `enforce`, with a global admin master switch (default off) and a per-profile guardrail configuration.
- **All-engine + fail-safe** — wired into all five engine classes at the same seam as the grounding gate; on an inbound block, the next-step recommender and online evaluation are suppressed so no LLM ever sees the injected text post-hoc; a zero-cost refusal turn is persisted.
- **Observable & reload-complete** — every scan emits a Live-Status guardrail card (live and on reload) and stamps its verdict on the turn.

**Three indirect-injection firewalls** close the paths the entry guard cannot see: a poisoned **memory** write is quarantined (stored but never recalled), a poisoned **retrieved document** is defused (kept but labelled untrusted data the model must not obey), and a poisoned **tool result** is flagged. All ride the same master switch and are deterministic + 0-token.

Detectors are deterministic (0-token regex/heuristics), so with the master switch off and the default `observe` mode Uderia's baseline output is byte-identical — guardrails are pure opt-in hardening.

[Architecture →](../Architecture/ANSWER_GATES_ARCHITECTURE.md) · Tier record → (`docs/roadmap/TIER_1.3_GUARDRAILS.md`)

## 🧭 Honesty Gate & Honest Failures

A deterministic gate compares what an answer claims about its own evidence with what the turn actually got, at zero token cost. The cases it checks, each named in the verdict it records:

| The answer … | Verdict |
|---|---|
| states a figure after every data query failed | `fabricated_after_failure` |
| asserts that something does not exist after every data query failed | `retrieval_failed` |
| reports a zero from a filtered query on a relation the turn never showed to hold data | `empty_relation` |
| reports a zero on a filter value nothing showed exists in that column | `unverified_literal` |
| presents a record (rows, field–value lines) that no successful query produced | `unsourced_record` |
| reports a completed write that the agent's authority denied this turn | `claimed_effect_after_denial` |
| says a write was prepared, confirmed or parked, and the turn holds no action record | `unbacked_act` |
| states a total when no aggregate query succeeded (a sum from a sample) | `sample_extrapolation` |
| answers a refused grain at a coarser one without saying so | `grain_substitution` |
| presents evidence as relating to a kind the declared bridge does not bind | `correspondence_overreach` |
| cites a library section it never opened, or names an identifier outside a closed vocabulary | `unread_citation` · `fabricated_identifier` |
| asserts specific claims from a knowledge base whose retrieval did not qualify | `rag` |

A query that succeeded with zero rows is an honest negative and passes untouched. A whole-answer read-only SELECT leaked as text is run through the agent's own governed read tool, so the user gets rows instead of SQL. A provider failure (no credits, rejected key, outage) is answered in one honest sentence and recorded as a failed turn — never a raw error, never a quiet pass. Modes `off · observe · annotate · review · enforce`; under `review` the withheld original is parked in the Assurance Inbox.

[Architecture →](../Architecture/ANSWER_GATES_ARCHITECTURE.md)

---

## 🔎 Preflight SQL Guards

Wrong SQL is stopped before the database sees it. Every raw-SQL tool call is checked against what the ontology declares and what the turn's own results have shown:

- joins off the declared key, and fan-out joins between two holders of the same foreign key
- joins across the model's subject areas
- dates stored as text but compared as strings
- literals the column can never hold
- columns and databases that do not exist

Each refusal names its own fix. When the fix is mechanical — a fan-out join rewritten to aggregate-then-join, or a text date given the correct `CAST … FORMAT` — Uderia applies it at no LLM cost. A zero-row result on an unverified filter value is flagged, so it is never reported as "there are none". The checks are deterministic, fail open, and default to `observe`.

[Architecture →](../Architecture/PREFLIGHT_SQL_ARCHITECTURE.md)

---

## 🔬 Agent Evaluation & Attestation

Every regulated buyer's hardest question is *"prove the agent didn't regress or hallucinate."* Uderia answers it continuously: **every turn and flow run is automatically scored** — in code, 0 tokens, on the same signed trajectory the EPC records — and **each verdict is itself Ed25519-signed and bound to the exact trajectory it grades**, so a quality claim is as tamper-evident as the execution it describes. Where the Grounding Gate and Guardrails *enforce* on the request path, this layer *observes and proves* — the assurance half of the trust story.

**How it works:**

1. **Deterministic-first scoring** — a profile-class-aware suite of code graders scores every turn on correctness, cost, safety, and grounding (CI-pure, runs off the request path, never alters or delays the answer). Optional diverse-family LLM-judge panels add the open-ended residue.
2. **Signed attestations** — each verdict's digest is bound to the turn's `chain_tip_hash` and signed with the **same Ed25519 provenance key** as the EPC, so a quality verdict cannot be forged or silently detached from the run it grades and is auditable alongside it.
3. **Agent health lens** — verdicts roll up into a per-agent health roster tracking quality, regressions, and cost over time, turning continuous scoring into an at-a-glance assurance signal per agent.
4. **Published proof** — a reproducible anti-hallucination benchmark plus a standalone offline verifier for a signed-attestation sample let an external reviewer confirm the binding with no Uderia install.

**Key properties:**
- **Tamper-evident verdicts** — Ed25519-signed and bound to the trajectory (forgery- and detachment-resistant)
- **Deterministic + non-blocking** — scoring is free and off the request path; it can never break or slow a turn
- **Calibrated judges** — LLM judges stay advisory (non-gating) until they agree with human labels (Cohen's κ ≥ 0.6)
- **Off by default for cost** — deterministic scoring on; the token-costing judge layer is opt-in and admin-controlled
- **63 scored dimensions** — 19 deterministic, 28 reliability, 10 judges and 6 flow measures, each with a logical name, moment and threshold

[Architecture →](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md) · [The 63 dimensions →](../Architecture/AGENT_QUALITY_DIMENSIONS.md) · [Proof-pack →](../proof-packs/A6_EVAL_OBSERVABILITY.md)

---

## 🔄 The Closed Trust Loop

The layers above *measure* and *enforce*; together they close a loop where **measured behaviour changes trust, trust can change enforcement, and every step is visible and verifiable** — the difference between selling trust as filtering (guardrail vendors) or reporting (governance vendors) and selling it as *closed-loop, verifiable trust*. Everything here ships **off or observe-only by default** (byte-identical baseline), every enforcement **fails closed**, and computing any of it costs **zero tokens**.

- **Behavioral Trust Score & Circuit Breaker** — an agent's trust reflects its *measured conduct*, not just a signature: recency-weighted pass rate + trust-critical checks, volume-gated, fused with its integrity badge into one composite state (Provisional / At-risk / Watch / Proven / Trusted) where integrity can only cap conduct, never promote it. Sustained bad conduct trips an opt-in **circuit breaker** that alerts the owner — or freezes the agent's *unattended* use (never interactive) until a human reviews.
- **Human Review (HITL)** — a fourth enforcement mode (`review`): withhold/redact exactly like `enforce`, but park the original for the owner to **approve or uphold**. Release is append-only — delivered history is never rewritten; an approved original surfaces in the answer's trust capsule, labelled as a human decision.
- **The Trust Journey & Answer Receipts** — every turn renders the assurance pipeline it passed through as a compact strip; every answer carries a consumer-facing **"Why trust this answer?"** capsule (scope, governed-vs-free-formed data lane, guard findings, the signed provenance chain drawn as a verifiable chain) — exportable as a portable **receipt** anyone can verify offline.
- **Assurance Inbox & Policy Backtesting** — trust incidents (breaker trips, quarantines, grounding blocks, review items) become a triageable queue instead of log lines. Before tightening any policy, **replay it over your real history** to see exactly what it would have blocked, masked, or escalated — deciding on evidence, not guesswork.
- **Live Red-Team & Signed Audit Pack** — probe an agent with a live adversarial corpus and score whether its guards actually held (opt-in, budget-capped; scoring is deterministic and free). Export a signed, offline-verifiable **audit pack** mapping your posture to EU AI Act / NIST AI RMF / GDPR controls.
- **The knowledge lane, on par — and beyond** — retrieval-augmented answers get every control the data lane earned: the grounding gate's **corpus-support trigger** (unsupported numbers/quotes/identifiers, second-chance retrieval, judge-adjudicated enforce, inline claim marks), **retrieval-sufficiency honesty** ("the knowledge base does not cover this" instead of invention), an **ingest firewall** with an earned corpus signature and one-click reversible **document quarantine**, an **exfil-URL guard** (novel + exfiltration-shaped links are defanged, never silently deleted), per-answer **source provenance** (integrity 🟢/🟠/⚪ · freshness · sensitivity), plus three further controls: retrieval-anomaly monitoring, per-agent sensitivity ceilings, and a source-consistency judge that surfaces when documents disagree. Proven by a deterministic, zero-token benchmark ([proof pack](../proof-packs/RAG_TRUST_HARDENING.md) · [Knowledge Retrieval →](../Architecture/KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md)).
- **An Assurance Guide that shows how each check works** ([Assurance Instruments →](../Architecture/ASSURANCE_INSTRUMENTS_ARCHITECTURE.md)) — every control names the properties it assesses, the modules it uses during and after the turn, and only the measures that share its code (marked **S**, linked to the module **M** they read); the grounding gate states the finding it acted on, and a provider failure reads as an honest failed turn — never as a quiet pass.
- **Spatial & compositional visibility** — the grounding scope is drawn as a boundary on the agent's knowledge graph with drift crossing it in red; the coordination trace tree carries a trust badge on every expert node, so a coordinator's answer visibly inherits its weakest link.

The complete operator runbook — the *why*, *what*, and *how* of the whole stack, with a graduated observe → enforce maturity ladder — is the single self-contained reference: **[Agent Trust & Assurance — Administrator Operations Guide](../manuals/AGENT_TRUST_AND_ASSURANCE.md)**.

Architecture: [Assurance Substrate](../Architecture/ASSURANCE_SUBSTRATE_ARCHITECTURE.md) (one assessment for both lanes) · [Trust Symmetry Map](../Architecture/TRUST_SYMMETRY_MAP.md) (mechanisms ↔ measurements) · [Assurance Module Plane](../Architecture/ASSURANCE_MODULE_PLANE_ARCHITECTURE.md) (third-party assurance modules) · [Building Assurance Modules](../manuals/BUILDING_ASSURANCE_MODULES.md)

---

## 🔵 The Living Trust Circle

The closed trust loop is not only enforced — it is **drawn**. The Trust Circle renders the loop as a living instrument inside Uderia: **eight stations** — Signed · Grounded · Guarded · Bounded · Judged · Trusted · Reviewed · Proven — arranged in a circle around your agent, each one a **live reading of real state** (never an illustration, never an invented composite score), pulsing as real turns pass through the gates.

- **Every station is operable** — click a station to jump straight to the surface that governs it (grounding config, guardrail policy, the Assurance Inbox, sign-off, and so on).
- **Play its life** — Uderia narrates an agent's biography from its own sealed records: birth, screened knowledge, sign-off, scored turns, human decisions — every sentence backed by an auditable record, closing with **how many times the loop actually closed**.
- **A miniature travels everywhere** — a compact glyph of the circle rides with every answer, every agent roster card, and the chat header, so an agent's current trust state is always in view.

Because each station reads live state rather than a stored number, the Trust Circle can never overstate trust: an absent signal is shown as an honest gap, not a green light.

Public story: **[trust.uderia.com — The Life of a Uderia Agent](https://trust.uderia.com/the-life-of-a-uderia-agent.html)** · operator detail in the [Agent Trust & Assurance runbook](../manuals/AGENT_TRUST_AND_ASSURANCE.md) · design: Agent Anatomy Panel (`docs/roadmap/AGENT_ANATOMY_PANEL.md`) · contract: [Trust Status Communication](../Architecture/TRUST_STATUS_COMMUNICATION.md) ([casebook](../Architecture/TRUST_STATUS_COMMUNICATION_CASEBOOK.md)) · reading guide: [Trust UX FAQ](../manuals/TRUST_UX_FAQ.md).

---

## Cross-cutting — Threat Model & Compliance

*The following span both pillars.*

## ⚠️ Threat Model

| Threat | Mitigation |
|---|---|
| Unrecorded prompt change | Every stored change is versioned (`prompt_versions`, with who and why); a release never overwrites a person's edit; the editor needs a signed Enterprise or Partner licence |
| License forgery | RSA-PSS 4096-bit signature cannot be forged without the private key |
| Execution log tampering | Ed25519-signed hash chain — any modification breaks chain verification |
| Step injection / removal | `previous_hash` chain linking + step count detect inserted or missing steps |
| Replay attack | Timestamps + chain context prevent reuse of old provenance steps |
| Cross-session tampering (Genie) | Child chain references create a Merkle-tree — all slave sessions linked into a single verifiable unit |
| Brute-force login | 5 failures → 15-min lockout + exponential progressive delay |
| Token compromise | Per-token DB revocation; 60-second LRU cache TTL limits blast radius |
| Credential leakage via API | `[REDACTED]` sentinel — secrets never returned in API responses |
| LLM API key theft | Per-user Fernet encryption with PBKDF2 key derivation (100k iterations, user UUID salt) |
| SAML assertion forgery | `signxml` verifies IdP certificate on every assertion; issuer + recipient + expiry validated |
| SAML replay | `NotOnOrAfter` expiry check; relay state consumed once |
| Privilege escalation via SSO | Group-to-tier re-evaluated on every login; all tier changes logged in `sso_sync_events` |
| Shell code execution escape | Two-layer isolation (registry-installed `uderia-shell` connector): admin governance + Docker container (non-root, no host FS, resource limits) per invocation |
| Man-in-the-middle LLM calls | Content hashes in EPC detect response substitution post-hoc |
| Master key compromise | `rotate_encryption_key.py` re-encrypts every stored secret (server stopped) |

---

## Enterprise Compliance

| Regulation | Requirement | How Uderia Addresses It |
|---|---|---|
| **EU AI Act** | Transparency and traceability for AI systems | EPC records every LLM call, tool selection, and response with Ed25519-signed cryptographic proof |
| **GDPR Art. 22** | Right to explanation of automated decisions | Provenance chain traces each decision from query to final answer |
| **GDPR Art. 30** | Records of processing activities | Session-level provenance with cross-turn integrity; SSO sync event log |
| **SOX** | Audit trails for financial reporting | Tamper-evident, offline-verifiable record of every AI-assisted operation |
| **ISO 27001** | Information security management | Versioned prompts, signed execution logs, key management procedures, access controls |
| **HIPAA** | Audit controls for access to ePHI | JWT/access token audit trail; session isolation per user UUID; consumption enforcement |
| **FedRAMP** | Cryptographic key management | Documented key rotation procedures; Ed25519 for signing; RSA-4096 for licenses |

Full technical details span the two pillars: [**Security Architecture (Deployment Security)**](../Architecture/SECURITY_ARCHITECTURE.md) — authentication systems (JWT, OAuth 2.0, OIDC, SAML 2.0), RBAC authorization, secrets management, prompt encryption, Execution Provenance Chain, audit logging, threat model, and enterprise compliance mapping (EU AI Act, GDPR, SOX, ISO 27001, HIPAA, FedRAMP); and [**Agent Ops & Evaluation (Agent Trust & Assurance)**](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md) — per-turn scoring, judges and signed attestations — with the gates themselves in [**Answer Gates**](../Architecture/ANSWER_GATES_ARCHITECTURE.md).
