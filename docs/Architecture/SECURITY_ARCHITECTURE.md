# Uderia Security Architecture

> **Scope — Pillar 1 of 2: Deployment Security.** Uderia's security model has two pillars.
> This document covers **Deployment Security** (information security: identity, data, secrets,
> IP, and the integrity of every action — ISO 27001 / SOC 2 / FedRAMP). The second pillar,
> **Agent Trust & Assurance** (AI governance: grounding gate, AI guardrails, agent evaluation
> & signed attestations — EU AI Act / NIST AI RMF), is documented in
> [AGENT_OPS_EVAL_ARCHITECTURE.md](AGENT_OPS_EVAL_ARCHITECTURE.md). The pillars meet at one
> cryptographic substrate: the **same Ed25519 key that signs each execution trajectory (the
> EPC, §6) also signs the evaluation verdict that grades it**. Both are summarized in the
> README [Security & Trust](../capabilities/SECURITY_AND_TRUST.md) capability page.

## Executive Summary

Uderia's **Deployment Security** pillar is a **multi-layer enterprise security architecture** spanning authentication, authorization, secrets management, cryptographic data protection, audit logging, and enterprise identity federation. The platform is designed from the ground up for zero-trust deployments where data sovereignty and accountability are non-negotiable.

Five complementary deployment-security systems work together to provide end-to-end trust guarantees (the Agent Trust & Assurance pillar adds the AI-output trust layer — see the scope note above):

| System | What it protects |
|---|---|
| **System Prompts and the Licence** | Versioned plain-text prompts, releases that never overwrite a person's edit, and the signed licence that activates the enterprise package |
| **Execution Provenance Chain (EPC)** | Integrity of every LLM decision, tool call, and response |
| **Authentication & Identity Federation** | User identity via passwords, OAuth, OIDC, SAML 2.0 SSO |
| **Secrets & Credential Management** | LLM API keys, SSO secrets, connector credentials, encrypted file stores |
| **Access Control & Consumption Governance** | Who can do what, and how much |

Together these establish a **zero-trust execution model**: the intelligence that drives the AI is cryptographically protected, and every action the AI takes is cryptographically recorded. This positions Uderia for enterprise compliance requirements including SOX audit trails, GDPR accountability, EU AI Act transparency mandates, and ISO 27001 information security management.

---

## Table of Contents

1. [Key Inventory](#1-key-inventory)
2. [Authentication Systems](#2-authentication-systems)
   - [JWT Session Tokens](#21-jwt-session-tokens)
   - [Long-Lived Access Tokens](#22-long-lived-access-tokens)
   - [Password Authentication](#23-password-authentication)
   - [OAuth 2.0 Social Login](#24-oauth-20-social-login)
   - [OIDC Enterprise SSO](#25-oidc-enterprise-sso-phase-1)
   - [SAML 2.0 Enterprise SSO](#26-saml-20-enterprise-sso-phase-2)
   - [Email Verification](#27-email-verification)
3. [Authorization & Access Control](#3-authorization--access-control)
   - [User Tiers](#31-user-tiers)
   - [Decorator Enforcement](#32-decorator-enforcement)
   - [Pane Visibility Controls](#33-pane-visibility-controls)
   - [Consumption Profiles & Rate Limiting](#34-consumption-profiles--rate-limiting)
4. [Secrets & Credential Management](#4-secrets--credential-management)
   - [Per-User LLM Credential Encryption](#41-per-user-llm-credential-encryption)
   - [Platform Connector Credentials](#42-platform-connector-credentials)
   - [SSO Client Secret Encryption](#43-sso-client-secret-encryption)
   - [REDACTED Sentinel Pattern](#44-redacted-sentinel-pattern)
   - [File Store Encryption](#45-file-store-encryption-prop-010-oct-2026)
5. [System Prompts and the Licence](#5-system-prompts-and-the-licence)
6. [Execution Provenance Chain (EPC)](#6-execution-provenance-chain-epc)
7. [Audit Logging](#7-audit-logging)
   - [JWT & Access Token Audit Trail](#71-jwt--access-token-audit-trail)
   - [Failed Login & Account Lockout](#72-failed-login--account-lockout)
   - [SSO Group Sync Audit Log](#73-sso-group-sync-audit-log)
   - [General Audit Log](#74-general-audit-log)
8. [Network Security](#8-network-security)
9. [Platform Connector Security](#9-platform-connector-security)
10. [Threat Model](#10-threat-model)
11. [Enterprise Compliance](#11-enterprise-compliance)
12. [Performance Impact](#12-performance-impact)
13. [Hardened Mode and the Federal Hardening Fixes](#13-hardened-mode-and-the-federal-hardening-fixes-oct-2026)

---

## 1. Key Inventory

All cryptographic keys reside in `/tda_keys/` with restricted filesystem permissions.

| File | Algorithm | Purpose | Auto-Generated | Rotation |
|---|---|---|:-:|---|
| `public_key.pem` | RSA-4096 | License signature verification | No (shipped) | Requires re-issuing all licenses |
| `license.key` | RSA-PSS signed JSON | License validation; its tier activates the enterprise package | No (issued) | New license file |
| `jwt_secret.key` | HMAC-SHA256 (256-bit) | JWT session token signing | Yes (first use) | `maintenance/regenerate_jwt_secret.py` |
| `provenance_key.pem` | Ed25519 (private PKCS8; encrypted at rest when `TDA_PROVENANCE_KEY_PASSPHRASE` is set) | EPC chain step signing | Yes (first use) | `maintenance/rotate_provenance_key.py` |
| `provenance_key.pub` | Ed25519 (public) | EPC chain offline verification | Yes (generated with private) | Rotated together |

**Environment variables that override file-based keys:**

| Variable | Purpose | Default |
|---|---|---|
| `TDA_JWT_SECRET_KEY` | Override `jwt_secret.key` | File-based key |
| `TDA_ENCRYPTION_KEY` | Master key for every stored secret (per-user credentials, connector credentials, OAuth tokens, SSO/SAML secrets, global TTS). Read from the environment or `.env`; change it only with `maintenance/rotate_encryption_key.py` (§13.1) | Dev default (refused in hardened mode) |
| `TDA_JWT_EXPIRY_HOURS` | JWT token lifetime | `24` |
| `TDA_MAX_LOGIN_ATTEMPTS` | Failed login lockout threshold | `5` |
| `TDA_LOCKOUT_DURATION_MINUTES` | Account lock duration | `15` |
| `TDA_PROGRESSIVE_DELAY_BASE` | Progressive delay base per failed attempt (seconds) | `2` |
| `TDA_PROGRESSIVE_DELAY_MAX` | Cap on progressive delay (seconds) | `30` |
| `TDA_PASSWORD_MIN_LENGTH` | Minimum password length | `8` |
| `TDA_SECURITY_PROFILE` | `hardened` turns on hardened mode (see §13) | unset |
| `TDA_PROVENANCE_KEY_PASSPHRASE` | Encrypts `provenance_key.pem` at rest (PKCS8, best available encryption); a plain key file is encrypted in place on first load | unset |
| `TDA_SESSION_IDLE_MINUTES` | Idle limit for browser sessions (browser lock + server backstop); `0` = off | `0`, or `30` in hardened mode |
| `TDA_AUDIT_RETENTION_DAYS` | Audit retention enforced by database triggers (floor 365) | `365` |
| `TDA_AUDIT_SYSLOG` | Ship audit events to syslog: `udp://host:514`, `tcp://host:6514` or `/dev/log` | unset |
| `TDA_AUDIT_JSONL` | Also write audit events as JSON lines to this file | unset |
| `TDA_OAUTH_PKCE` | `0` disables PKCE on social OAuth | on |
| `TDA_SAML_ALLOW_IDP_INITIATED` | Accept unsolicited (IdP-initiated) SAML responses | off |
| `TDA_DB_BACKUP_DIR` / `TDA_DB_BACKUP_KEEP` | Where the daily verified database backup is written, and how many are kept (§13) | `backups/db` beside the database / `7` |

---

## 2. Authentication Systems

### 2.1 JWT Session Tokens

JWT tokens authenticate browser sessions via the `Authorization: Bearer <token>` header.

**Cryptographic algorithm:** HS256 (HMAC-SHA256)

**Token structure:**
```json
{
  "user_id": "uuid",
  "username": "string",
  "exp": "unix timestamp",
  "iat": "unix timestamp",
  "jti": "secrets.token_urlsafe(32)"
}
```

**Lifecycle and storage:**
- Default expiry: 24 hours (`TDA_JWT_EXPIRY_HOURS`)
- Token hash (SHA-256) stored in `auth_tokens` table for revocation tracking
- In-memory LRU cache (10,000 entries, 60-second TTL) eliminates 2 DB queries per request at steady state
- Per-token revocation via `AuthToken.revoked` flag; cache invalidated on revoke
- IP address and user agent recorded at issuance

**Internal service tokens:** Used by the Genie coordinator for child session creation. 30-minute expiry, marked `"internal": True` in payload, not stored in the database.

**Files:** `src/trusted_data_agent/auth/security.py`, `src/trusted_data_agent/auth/middleware.py`

---

### 2.2 Long-Lived Access Tokens

API access tokens authenticate programmatic REST API clients without session management overhead.

**Format:** `tda_` prefix + `secrets.token_urlsafe(24)` (~40 characters total)

**Storage model:**
- Full token displayed **once** at creation — never stored in plaintext
- SHA-256 hash stored in `access_tokens` table
- First 12 characters (`token_prefix`) kept for UI display ("tda_abc12...")

**`access_tokens` table schema:**
```sql
id            UUID PRIMARY KEY
user_id       TEXT REFERENCES users(id)
token_prefix  VARCHAR(10) INDEXED        -- for display only
token_hash    VARCHAR(255) UNIQUE        -- SHA-256 of full token
name          VARCHAR(100)               -- human-readable label
created_at    DATETIME
expires_at    DATETIME (NULL = never)
last_used_at  DATETIME                   -- updated on each successful auth
use_count     INTEGER DEFAULT 0          -- incremented per auth
last_ip_address VARCHAR(45)              -- IPv6-compatible, last request IP
revoked       BOOLEAN DEFAULT FALSE
revoked_at    DATETIME
```

**Files:** `src/trusted_data_agent/auth/security.py`

---

### 2.3 Password Authentication

**Hashing:** bcrypt with 12 rounds (work factor). Per-password random salt auto-generated by bcrypt.

**Verification:** Constant-time comparison via bcrypt's built-in `checkpw()`.

**Strength requirements** (validated at registration and password change):
- Minimum length: `TDA_PASSWORD_MIN_LENGTH` (default 8)
- At least one uppercase letter
- At least one lowercase letter
- At least one digit

**Password reset token:**
```sql
id         UUID PRIMARY KEY
user_id    TEXT REFERENCES users(id)
token_hash VARCHAR(255) UNIQUE INDEX  -- SHA-256 of reset token
expires_at DATETIME (1 hour from creation)
created_at DATETIME
used       BOOLEAN DEFAULT FALSE
used_at    DATETIME
```

Reset tokens are single-use; `is_valid()` checks `used IS FALSE AND expires_at > now()`.

**Failed login and lockout:** See [Section 7.2](#72-failed-login--account-lockout).

**Files:** `src/trusted_data_agent/auth/security.py`

---

### 2.4 OAuth 2.0 Social Login

Supported providers: **Google, GitHub, Microsoft, Discord, Okta** (extensible via `oauth_handlers.py`).

**Flow:** Standard OAuth 2.0 Authorization Code. Access token exchanged for provider user info. JIT (Just-In-Time) user provisioning on first login.

**`oauth_accounts` table:**
```sql
id                  UUID PRIMARY KEY
user_id             TEXT REFERENCES users(id)
provider            VARCHAR(50)         -- 'google', 'github', etc.
provider_user_id    VARCHAR(255)        -- provider's unique ID
provider_email      VARCHAR(255)
provider_name       VARCHAR(255)
provider_picture_url TEXT
provider_metadata   JSON                -- additional profile data
created_at          DATETIME
updated_at          DATETIME
last_used_at        DATETIME
UNIQUE(provider, provider_user_id)
```

**Security properties:**
- No OAuth tokens stored beyond the authorization code exchange
- Email verification optional per provider
- Duplicate email detection across authentication methods

**Files:** `src/trusted_data_agent/auth/oauth_handlers.py`, `src/trusted_data_agent/api/auth_routes.py`

---

### 2.5 OIDC Enterprise SSO (Phase 1)

Generic OIDC federation via `/.well-known/openid-configuration` discovery. Supports any standards-compliant IdP (Okta, Azure AD, Auth0, Keycloak, Google Workspace, etc.).

**`sso_configurations` table:**
```sql
id               UUID PRIMARY KEY
name             TEXT NOT NULL
provider         TEXT DEFAULT 'oidc'
issuer_url       TEXT NOT NULL          -- used for discovery doc fetch
client_id        TEXT NOT NULL
client_secret    TEXT                   -- Fernet-encrypted
discovery_doc    TEXT                   -- cached JSON, TTL 3600s
discovery_cached_at DATETIME
scopes           TEXT DEFAULT '["openid","profile","email"]'
email_claim      TEXT DEFAULT 'email'
name_claim       TEXT DEFAULT 'name'
groups_claim     TEXT                   -- e.g. 'groups'
sub_claim        TEXT DEFAULT 'sub'
group_tier_map   TEXT                   -- JSON: {"GroupName": "admin"}
default_tier     TEXT DEFAULT 'user'
enabled          INTEGER DEFAULT 1
auto_provision_users INTEGER DEFAULT 1
require_email_verification INTEGER DEFAULT 0
button_label     TEXT
icon_url         TEXT
created_at       DATETIME
updated_at       DATETIME
```

**`sso_sessions` table** (for back-channel logout):
```sql
id              UUID PRIMARY KEY
user_uuid       TEXT REFERENCES users(id)
sso_config_id   TEXT REFERENCES sso_configurations(id)
id_token_hash   TEXT  -- SHA-256 of id_token for revocation lookup
sid             TEXT  -- IdP session ID (from token claims)
sub             TEXT  -- IdP subject
issued_at       DATETIME
expires_at      DATETIME
revoked         BOOLEAN DEFAULT FALSE
revoked_at      DATETIME
```

**Validation pipeline:**
1. Fetch and cache discovery document (TTL: 3600 seconds)
2. Exchange authorization code for `id_token` and `access_token` (using Fernet-decrypted `client_secret`)
3. Validate `id_token` via `python-jose`: signature, `iss`, `aud`, `exp`, nonce
4. Extract claims: email, name, groups
5. Apply group-to-tier mapping (highest-privilege group wins)
6. JIT-provision user or update existing user's tier

**Back-channel logout:** IdP posts logout token → `id_token_hash` lookup → session revoked

**API responses:** `client_secret` always returned as `"[REDACTED]"` — the literal secret never leaves the server.

**Files:** `src/trusted_data_agent/auth/oidc_provider.py`, `src/trusted_data_agent/api/auth_routes.py`

---

### 2.6 SAML 2.0 Enterprise SSO (Phase 2)

Full SAML 2.0 Service Provider implementation supporting enterprise IdPs (Active Directory FS, Okta, Azure AD, OneLogin, PingIdentity).

#### Service Provider Role

Uderia acts as the SP. It:
- Generates signed `AuthnRequest` via HTTP-Redirect binding (raw deflate + base64 + URL encoding)
- Exposes SP metadata XML at `GET /api/v1/auth/saml/<id>/metadata` (paste into IdP configuration)
- Receives signed `SAMLResponse` via HTTP-POST binding at the Assertion Consumer Service (ACS)

**`saml_configurations` table:**
```sql
id                TEXT PRIMARY KEY
name              TEXT NOT NULL
sp_entity_id      TEXT NOT NULL       -- SP's unique identifier
sp_acs_url        TEXT                -- computed from base URL if null
sp_private_key    TEXT                -- PEM, Fernet-encrypted
sp_certificate    TEXT                -- PEM public cert
idp_entity_id     TEXT NOT NULL       -- IdP's unique identifier
idp_sso_url       TEXT NOT NULL       -- IdP login endpoint
idp_slo_url       TEXT                -- IdP logout endpoint (optional)
idp_certificate   TEXT NOT NULL       -- PEM cert for assertion signature verification
email_attr        TEXT DEFAULT 'email'
name_attr         TEXT DEFAULT 'displayName'
groups_attr       TEXT                -- attribute name carrying group list
default_tier      TEXT DEFAULT 'user'
group_tier_map    TEXT                -- JSON: {"GroupName": "tier"}
auto_provision_users INTEGER DEFAULT 1
enabled           INTEGER DEFAULT 1
button_label      TEXT
icon_url          TEXT
display_order     INTEGER DEFAULT 0
created_at        DATETIME
updated_at        DATETIME
```

#### AuthnRequest Generation

```
1. Build SAML 2.0 AuthnRequest XML
2. UTF-8 encode
3. Deflate with raw DEFLATE (zlib, stripping 2-byte header and 4-byte checksum)
4. Base64 encode
5. URL encode
6. Append as SAMLRequest= query parameter to idp_sso_url
7. Add RelayState= (random value bound to the AuthnRequest ID; consumed once at the ACS)
```

#### Assertion Validation Pipeline

On ACS POST (`POST /api/v1/auth/saml/<id>/acs`) — every step fails closed (hardened Oct 2026, §13):

1. **Decode** SAMLResponse from base64
2. **Parse** XML with a hardened `lxml` parser — no DTDs, no entity expansion, no network (DOCTYPE refused: XXE)
3. **Top-level `StatusCode`** must be `Success`
4. **Verify XML digital signature** using `signxml.XMLVerifier` and the configured IdP certificate (PEM)
   - Rejects any response/assertion not signed by the expected IdP
   - **All further facts are read from the element signxml returns as signed** — never from the original tree (closes XML signature wrapping)
5. **Validate assertion fields:**
   - `Issuer` matches `idp_entity_id` (required)
   - `Conditions` time window — `NotOnOrAfter` required, `NotBefore` honoured, clock skew tolerance 300 s (`_CLOCK_SKEW`)
   - `AudienceRestriction` must name this SP's entity id
   - A bearer `SubjectConfirmationData` whose `Recipient` is our ACS, not expired, and whose `InResponseTo` is an AuthnRequest this SP issued (IdP-initiated responses are refused unless `TDA_SAML_ALLOW_IDP_INITIATED` is set)
   - **Replay cache:** an assertion ID is accepted once until it expires (per process)
6. **Extract attributes:** email, name, groups from the configured attribute names
7. **Resolve effective tier:** highest-privilege group mapping wins (see below)
8. **JIT-provision** user or sync existing user's tier/groups
9. **Issue JWT** and redirect to `/?token=<jwt>&method=saml`

Gate: `test/test_saml_hardening.py`.

On validation failure, user is redirected to `/login?error=<reason>` — no sensitive details exposed.

#### Group-to-Tier Resolution

Groups are extracted from the SAML assertion. The effective tier is the highest-privilege tier across all groups the user belongs to:

```python
_tier_order = {'user': 0, 'developer': 1, 'admin': 2}
tier = cfg['default_tier']  # starting point
for group in user_groups:
    mapped = group_tier_map.get(group)
    if mapped and _tier_order[mapped] > _tier_order[tier]:
        tier = mapped
# Result: highest privilege wins
```

This is **always re-evaluated on every login** — tier changes in the IdP take effect on the user's next authentication, with no manual intervention required.

#### SP Private Key Security

The SP's private key (used to sign AuthnRequests to IdPs that require signed requests) is stored Fernet-encrypted in the database. The API never returns the raw key — responses show `"[REDACTED]"` to confirm the key is configured without exposing its value.

**Files:** `src/trusted_data_agent/auth/saml_provider.py`, `src/trusted_data_agent/api/auth_routes.py`

---

### 2.7 Email Verification

**`email_verification_tokens` table:**
```sql
id                UUID PRIMARY KEY
user_id           TEXT REFERENCES users(id)
token_hash        VARCHAR(255) UNIQUE INDEX  -- SHA-256 of verification token
email             VARCHAR(255) INDEXED
verification_type TEXT  -- 'oauth', 'signup', 'email_change'
oauth_provider    TEXT  -- 'google' etc. when type='oauth'
expires_at        DATETIME (24 hours from creation)
verified_at       DATETIME (NULL until verified)
created_at        DATETIME
```

Tokens are single-use and time-limited. `is_valid()` checks both `verified_at IS NULL` and `expires_at > now()`.

---

## 3. Authorization & Access Control

### 3.1 User Tiers

Three-tier hierarchy stored as `User.profile_tier (VARCHAR(20))` and `User.is_admin (BOOLEAN)`:

| Tier | `profile_tier` | `is_admin` | Capabilities |
|---|---|:-:|---|
| **User** | `'user'` | false | Execute queries, manage own profiles and collections, view own sessions |
| **Developer** | `'developer'` | false | All User capabilities + create/modify profiles, configure data sources, access REST API |
| **Admin** | `'admin'` | true | All Developer capabilities + user management, system configuration, platform connector governance, security settings, compliance reporting |

Tier is re-evaluated on every SSO login. Manual tier changes by admins take effect immediately.

---

### 3.2 Decorator Enforcement

All route protection is enforced by middleware decorators in `src/trusted_data_agent/auth/middleware.py`:

**`@require_auth`:**
- Extracts `Authorization: Bearer <token>` header
- Tries access token format (`tda_` prefix) first, then JWT
- Injects `current_user: User` as first positional argument
- Returns `401 Unauthorized` if unauthenticated

**`@require_admin`:**
- Full authentication (same as `@require_auth`)
- Additionally checks `current_user.is_admin == True`
- Returns `403 Forbidden` if authenticated but not admin
- **Note:** Do not stack `@require_auth` on top of `@require_admin` — both inject `current_user`, causing double injection. Use only `@require_admin` for admin routes.

**`@optional_auth`:**
- Attempts authentication; passes `current_user=None` if unauthenticated
- Used for public endpoints that behave differently when authenticated

---

### 3.3 Pane Visibility Controls

The sidebar's views are governed per tier by the `pane_visibility` table:

```sql
id               UUID PRIMARY KEY
pane_id          VARCHAR(50) UNIQUE  -- 'conversation', 'credentials', 'agents', 'executions',
                                    --  'ontology', 'rag-maintenance', 'components',
                                    --  'marketplace', 'admin'
pane_name        VARCHAR(100)       -- written from the catalogue, never edited by hand
visible_to_user      BOOLEAN
visible_to_developer BOOLEAN
visible_to_admin     BOOLEAN
description      VARCHAR(255)       -- written from the catalogue
display_order    INTEGER            -- written from the catalogue (sidebar order)
created_at, updated_at DATETIME
```

The three `visible_to_*` flags are the only administrator-owned columns. Names, descriptions and order come from the pane catalogue, `core/pane_catalog.py` — the one list of the sidebar's views (Reasoning · Anatomy · Trust · Awareness · Ontology · Intelligence · Components · Marketplace · Administration) — and `sync_panes` writes them onto the rows on every read, inserting any view that has no row yet (with its default visibility). A view whose optional feature is absent (Ontology without the enterprise suite) is not returned; its row is kept, so the admin's choice survives the feature being switched back on. `admin` is always visible to admins. Admins manage the flags in **Administration → Panes** (`/api/v1/admin/panes`, see the REST reference §3.1.12a). Gate: `test/test_pane_catalog.py`.

Pane visibility hides navigation; it is not an authorization boundary. The endpoints behind each view enforce their own tier and ownership checks.

---

### 3.4 Consumption Profiles & Rate Limiting

#### Consumption Profiles (Per-User Token Quotas)

Consumption profiles govern per-user resource limits and are assigned to users by admins.

**`consumption_profiles` table:**
```sql
id                     INTEGER PRIMARY KEY
name                   VARCHAR(100) UNIQUE INDEX
description            TEXT
prompts_per_hour       INTEGER DEFAULT 100
prompts_per_day        INTEGER DEFAULT 1000
config_changes_per_hour INTEGER DEFAULT 10
input_tokens_per_month  INTEGER (NULL = unlimited)
output_tokens_per_month INTEGER (NULL = unlimited)
is_default             BOOLEAN INDEXED
is_active              BOOLEAN
created_at, updated_at DATETIME
```

**One resolver, every path.** The limits that apply to a user are resolved when a check runs, by `auth/consumption_limits.py:effective_limits`: an admin is exempt; Global Override's settings apply when it is on (no token limits); otherwise the user's assigned profile, else the default profile; no profile at all is unlimited. The UI routes' `ConsumptionEnforcer` and the per-turn `ConsumptionManager.check_rate_limits` / `check_token_quota` both read it, so a reassignment, an edited profile or Global Override takes effect on the next prompt. The limits cached on `user_consumption` are kept in step for the admin views and are never what a check compares against. **Configuration changes per hour** are enforced on the ten agent, LLM-configuration and MCP-server write routes (`auth/config_change_limit.py:limit_config_changes`): 429 over the limit, each successful change audited as `config_change`, which is the count. Gate: `test/test_consumption_limits.py`.

**Enforcement fails open, loudly:** if the consumption check itself throws (e.g. its database is unavailable) the turn proceeds — an infrastructure hiccup must not lock every user out — but never silently: the end user's Live Status shows a *Security Warning*, `APP_STATE['consumption_enforcement_bypasses']` counts it, and `core/quota_health.report_bypass` logs CRITICAL and banners every connected admin on the first bypass and at most every 15 minutes while it continues (decision 2026-10-30: fail-open kept, made operator-visible).

#### IP-Based Rate Limiting

Token-bucket algorithm protecting authentication endpoints:

| Endpoint class | Default limit |
|---|---|
| Login | 5 per minute per IP |
| Registration | 3 per hour per IP |
| API, without a valid credential | 60 per minute per IP |
| User prompts | 100 per hour per user |

The API limit is a `before_request` hook (`rate_limiter.enforce_ip_api_limit`) over `/api/` requests that carry no valid credential: a Bearer token or a `token=` parameter is validated, not merely present, and an access token is checked read-only. Signed-in traffic is governed per user; the notification stream (`/api/notifications/`, authenticated by a single-use ticket) is not counted. All IP limits follow the *Enable Rate Limiting* switch.

IP extraction priority: `X-Forwarded-For` → `X-Real-IP` → `remote_addr`

Rate limits are configurable by admins at `Administration → App Config → Security & Rate Limiting`.

**Files:** `src/trusted_data_agent/auth/consumption_limits.py`, `consumption_enforcer.py`, `consumption_manager.py`, `config_change_limit.py`, `rate_limiter.py`

---

## 4. Secrets & Credential Management

### 4.1 Per-User LLM Credential Encryption

LLM provider API keys are encrypted per user using keys derived from the deployment's master key.

**Key derivation (PBKDF2-HMAC-SHA256):**
```python
kdf = PBKDF2HMAC(
    algorithm=SHA256(),
    length=32,           # 256-bit Fernet key
    salt=user_id.encode(),   # Per-user salt (user UUID as bytes)
    iterations=100_000,  # NIST-recommended iteration count
)
key = base64.urlsafe_b64encode(kdf.derive(MASTER_ENCRYPTION_KEY.encode()))
```

**Encryption:** Fernet (AES-128-CBC + HMAC-SHA256 with timestamp). The Fernet timestamp allows detecting token age but does not enforce expiry.

**Master key:** `TDA_ENCRYPTION_KEY` (environment or `.env`). Set it on a new installation before any secret is stored; on an existing one, change it only with `maintenance/rotate_encryption_key.py`, which re-encrypts every stored secret under all three key families with the server stopped (§13.1). The older `auth.encryption.rotate_encryption_key()` covers `user_credentials` only and must not be used on its own.

**`user_credentials` table:**
```sql
id                   UUID PRIMARY KEY
user_id              TEXT REFERENCES users(id)
provider             VARCHAR(50)   -- 'Amazon', 'Google', 'OpenAI', etc.
credentials_encrypted TEXT          -- Fernet ciphertext
created_at, updated_at DATETIME
UNIQUE(user_id, provider)
```

**Files:** `src/trusted_data_agent/auth/encryption.py`

---

### 4.2 Platform Connector Credentials

Admin-configured credentials for platform connectors (browser, web search, Google Workspace, etc.) use a shared Fernet key derived from the same master key but with a **frozen salt**.

```python
salt = b'platform_mcp_registry'  # INTENTIONALLY FROZEN
kdf = PBKDF2HMAC(algorithm=SHA256(), length=32, salt=salt, iterations=100_000)
key = base64.urlsafe_b64encode(kdf.derive(TDA_ENCRYPTION_KEY.encode()))
return Fernet(key)
```

**Critical:** The salt `b'platform_mcp_registry'` is intentionally frozen even though the module was renamed. Changing it would make all existing admin credentials permanently unreadable. Do not change the salt.

**Files:** `src/trusted_data_agent/core/platform_connector_registry.py`

---

### 4.3 SSO Client Secret Encryption

Both OIDC `client_secret` and SAML `sp_private_key` are encrypted with the same `_platform_fernet()` function (frozen-salt Fernet) before storage.

Encryption happens at create/update time in `oidc_provider.py` and `saml_provider.py`. The Fernet-encrypted ciphertext is stored in the database.

---

### 4.4 REDACTED Sentinel Pattern

All API responses that would expose a sensitive field instead return the string literal `"[REDACTED]"`. This communicates to the client that the credential is configured without revealing its value.

**Fields using this pattern:**

| Resource | Field | Route |
|---|---|---|
| OIDC configuration | `client_secret` | `GET /api/v1/auth/sso/configurations`, `GET /api/v1/auth/sso/configurations/<id>` |
| SAML configuration | `sp_private_key` | `GET /api/v1/auth/saml/configurations`, `GET /api/v1/auth/saml/configurations/<id>` |
| Access tokens | Full token value | `POST /api/v1/auth/access-tokens` (one-time display) |

**Update guard:** When a PUT request sends `"[REDACTED]"` for one of these fields, the server skips re-encryption — the existing encrypted value in the database is preserved unchanged. Only a new, non-`"[REDACTED]"` value triggers encryption and storage update.

---

### 4.5 File Store Encryption (PROP-010, Oct 2026)

Everything a session keeps — kept tables, charts, answers, every chat attachment — is stored in an
admin-configured file store (local disk, S3, Azure Blob, Google Cloud Storage), **encrypted before
it leaves the process** (`filestore/crypto.py`):

- **Envelope encryption.** Each file has its own random 256-bit data key; content is AES-256-GCM
  in 1 MB chunks whose associated data binds the header, the chunk counter and the last-chunk
  flag, so a reordered, dropped or truncated object fails to decrypt.
- **Key wrapping.** The data key is wrapped by a key-encryption key derived from the deployment's
  master key with HKDF under its own context (never shared with credential derivation) — or,
  when configured, by an **external key manager** (`filestore/kms.py`: AWS KMS, Azure Key Vault,
  GCP KMS, HashiCorp Vault Transit; Administration → Components → Files → Keys; test-before-use).
  An externally wrapped key does not depend on the master key, so master-key rotation leaves it
  alone; rotation rewraps data keys only, no object is rewritten.
- **Content fingerprint** is a keyed HMAC of the plaintext, so provenance can name a file's
  content while the store, holding only ciphertext, cannot tell when two files are identical.
- **One audited exit.** Download (and every export, `FilesKeep.deliver`) goes through one path
  under the tier's download policy, audit-logged.

Full contract: [FILES_ARCHITECTURE.md](FILES_ARCHITECTURE.md).

---

## 5. System Prompts and the Licence

The system prompts are **plain text** (since 9 Nov 2026; the licence-tier encryption of earlier releases is retired). Their integrity rests on the repository and the database, not on a cipher: the fifteen defaults are tracked files (`schema/default_prompts/*.txt`) shipped with the code, every stored change is versioned (`prompt_versions`, with who and why), per-user and per-profile overrides are a separate layer, and a prompt a person edited is never overwritten by a release. The editor that writes overrides is part of the enterprise package. Full model: [PROMPT_ARCHITECTURE.md](PROMPT_ARCHITECTURE.md).

### 5.1 The licence

```json
{
  "payload": {
    "holder": "customer@company.com",
    "issued_at": "ISO 8601 timestamp",
    "expires_at": "ISO 8601 timestamp",
    "tier": "Enterprise | Partner"
  },
  "signature": "hex-encoded RSA-PSS signature of the payload"
}
```

| Property | Value |
|---|---|
| Signing | RSA-PSS, 4096-bit key, SHA-256, MGF1 — verified with `tda_keys/public_key.pem` |
| Where | `tda_keys/license.local.key` (the deployment's own, gitignored) → `tda_keys/license.key` (the repository's); `TDA_LICENSE_FILE` overrides (`core/license_file.license_path`) |
| Check | `core/license_file.validate` — the ONE check: signature, then expiry; read once at start |
| Effect | its tier activates the **enterprise package** (`uderia-enterprise`'s `register()` refuses to install the overlay without it) and the prompt editor (`core/enterprise.is_licensed`) |
| Without | the deployment is `Community` and runs the public core; an invalid or expired file is logged as an error and ignored — a deployment never refuses to start over its licence |

### 5.2 Conversion of databases the former encryption left

A database that still holds Fernet-encrypted prompt rows is converted once at start (`agent/prompt_migration.py`): every row a licence the deployment can see opens is written back as plain text in one transaction, history and versions untouched; a row no key opens is left and the log names the recovery. Gate: `test/test_prompt_migration.py`.

**Files:** `src/trusted_data_agent/core/license_file.py`, `src/trusted_data_agent/core/enterprise.py`, `src/trusted_data_agent/agent/prompt_defaults.py`, `src/trusted_data_agent/agent/prompt_migration.py`

---

## 6. Execution Provenance Chain (EPC)

The EPC creates an immutable, cryptographically signed audit trail covering every LLM decision, tool call, and response. It enables offline verification that no step was injected, modified, or replayed.

### 6.1 Cryptographic Primitives

| Operation | Algorithm | Output Size |
|---|---|---|
| Content hashing | SHA-256 | 64 hex characters |
| Chain linking | SHA-256 of `{index}:{type}:{content_hash}:{prev_hash}` | 64 hex characters |
| Step signing | Ed25519 | 64-byte signature (base64) |
| Key fingerprint | SHA-256 of Ed25519 public key raw bytes | 64 hex characters |

### 6.2 Chain Structure

Each execution step becomes a provenance record:

```json
{
  "step_id": "uuid",
  "step_index": 0,
  "step_type": "query_intake | strategic_plan | tool_call | tool_result | ...",
  "timestamp": "ISO 8601",
  "content_hash": "sha256(content[:4096])",
  "previous_hash": "chain_hash of prior step (or GENESIS_HASH for turn 1 step 0)",
  "chain_hash": "sha256('{index}:{type}:{content_hash}:{previous_hash}')",
  "signature": "base64(Ed25519.sign(chain_hash))",
  "content_summary": "≤200 characters human-readable"
}
```

Content is hashed but never stored — sensitive data (queries, responses, tool outputs) does not appear in provenance records.

**Genesis hash:** `"0000...0000"` (64 zeros) for the first step of the first turn in a session.

### 6.3 Cross-Turn Chain Linking

The first step of each subsequent turn includes `"previous_turn_tip_hash"` pointing to the final step hash of the prior turn. This creates a hash chain spanning the entire session:

```
Turn 1: [query_intake] → [strategic_plan] → [tool_call] → [complete]
                                                                ↓ tip
Turn 2: [query_intake] → [llm_call] → [response] → [complete]
           ↑ previous_turn_tip = Turn 1 tip
```

### 6.4 Cross-Session Linking (Genie Coordinator)

When the Genie coordinator spawns child sessions, each child session's chain tip is recorded as a `child_chain_ref` step in the parent's provenance chain. This creates a Merkle-tree-like structure linking the entire multi-session execution into a single verifiable unit.

### 6.5 Step Coverage

Every engine opens with `query_intake` + `profile_resolve` and closes with `turn_complete`:

| Profile Class | Steps Recorded (between intake and `turn_complete`) |
|---|---|
| **Optimize** (`tool_enabled`) | `strategic_plan`, `plan_rewrite`, `tactical_decision`, `tool_call`, `tool_result`, `self_correction` |
| **Ideate** (`llm_only`) | `llm_call`, `llm_response` |
| **Focus** (`rag_focused`) | `rag_search`, `rag_results`, `rag_synthesis` (`llm_response` on the no-results path) |
| **Ideate + MCP** (ReAct, `llm_only` + tools) | `agent_tool_call`, `agent_tool_result`, `agent_tool_rewrite` |
| **Coordinate** (`genie`) | `coordinator_dispatch`, `child_chain_ref`, `coordinator_synthesis` |

Sealed at `ProvenanceChain.finalize()` on any engine when the turn produced them: `compute_step`
(a `TDA_Compute` receipt), `obda.consumption` (a data product read), `action.performed` (an
OBDA-ACT write), `llm_recovery` (a provider fallback), `outcome_draft`, and `trust.bom` (the
turn's bill of materials — [EVIDENCE_ARCHITECTURE.md](EVIDENCE_ARCHITECTURE.md) §4). Failures
are recorded as `error:<type>` steps.

### 6.6 Provenance Metadata (Per Turn)

```json
{
  "chain_version": 1,
  "key_fingerprint": "sha256hex of Ed25519 public key",
  "profile_type": "tool_enabled | genie | ...",
  "session_id": "uuid",
  "turn_number": 1,
  "user_uuid": "uuid",
  "step_count": 8,
  "chain_root_hash": "hash of first step",
  "chain_tip_hash": "hash of last step",
  "previous_turn_tip_hash": "cross-turn link",
  "sealed": true
}
```

### 6.7 Three Verification Levels

1. **Chain Integrity** (offline-capable) — verifies hash linking, SHA-256 computations, and Ed25519 signatures using only the public key. No access to session data required.

2. **Content Verification** — recomputes content hashes from actual session data files and confirms they match provenance records. Detects content tampering after the fact.

3. **Session Integrity** — verifies all turns link correctly via `previous_turn_tip_hash` and Genie's cross-session `child_chain_ref` references. Detects turn injection or removal.

### 6.8 REST API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/sessions/{id}/provenance` | Full provenance for all turns |
| `GET` | `/api/v1/sessions/{id}/provenance/turn/{n}` | Single turn provenance |
| `POST` | `/api/v1/sessions/{id}/provenance/verify` | Verify chain integrity (returns pass/fail + details) |
| `GET` | `/api/v1/sessions/{id}/provenance/export` | Download full JSON for offline audit |
| `GET` | `/api/v1/provenance/public-key` | Download Ed25519 public key PEM for independent verification |

### 6.9 Key Properties

- **Zero new dependencies** — uses the `cryptography` library already present in the project
- **Negligible overhead** — ~100 microseconds per step (hash + sign), invisible against LLM latency
- **Degraded mode** — if the signing key is unavailable, hashes are still recorded (unsigned); execution continues unblocked
- **Key rotation** — `maintenance/rotate_provenance_key.py` generates new keys; existing chains remain verifiable via stored `key_fingerprint` (every key that signed is kept in `trust_signing_keys`)
- **Key custody (opt-in)** — `TDA_EVIDENCE_SIGNER=command` moves signing off the host (HSM, cloud KMS, Vault transit); every returned signature is verified before use. Signed ledger checkpoints can be anchored externally (`TDA_EVIDENCE_ANCHOR`). See [EVIDENCE_ARCHITECTURE.md](EVIDENCE_ARCHITECTURE.md) §10
- **Backward compatible** — sessions without provenance data handled gracefully

### 6.10 Trust Membrane attestations (Tier 5.2)

The same Ed25519 provenance key signs a third artifact class — **trust attestations** (Trust Membrane (`docs/roadmap/TIER_5.2_TRUST_MEMBRANE.md`), `core/trust/`). Where the EPC signs *what an agent did* and the eval attestation signs *how good it was*, a trust attestation signs *that an agent is composed only of validated ingredients*.

> **Packaging note:** the Trust Membrane ships as part of the **Ontology Suite** — one physically-isolatable enterprise feature (Platform Ontology UX + Trust Membrane + the REST for both), gated by a single switch (`core/enterprise.py`; ON for all today, one-line flip to Enterprise-only). See [PLATFORM_ONTOLOGY_ARCHITECTURE.md §11](PLATFORM_ONTOLOGY_ARCHITECTURE.md).

- **Compositional, tamper-evident.** An agent's badge (⚪ unsigned · 🟢 signed · 🟠 signed-but-changed) is computed over a content hash of its ingredients (ontology, skills, …); editing any ingredient changes the composition hash → the agent's signature no longer matches → 🟠 automatically. `store.verify()` re-checks the stored signature against the reconstructed payload with the provenance **public** key — offline-verifiable, same as the EPC.
- **Governed sign-off.** `POST /v1/trust/*/sign-off` is **ownership-gated** for every owned kind (the owner is resolved per kind and the check fails closed — `trust_routes._assert_owner`); the platform-wide kinds (`skill`, `extension`) stay **tier-gated** (signer ∈ `approver_tiers`, default admin); **separation of duties** (no self-sign under an approval policy) reuses the Tier 4.3 governance policy. Deployment attestation is ownership-only. An agent can be signed only when *every* ingredient is already signed (the bottom-up gate → HTTP 409). See [TRUST_MEMBRANE_ARCHITECTURE.md](TRUST_MEMBRANE_ARCHITECTURE.md) §3b, §7b.
- **Fail-safe & additive.** Signing degrades gracefully (`signed=False`) if the key is unavailable; with no attestations every agent reads ⚪ (byte-identical to today). Trust is **orthogonal to access** — the badge says *validated*, not *authorized*; authN/authZ are unchanged (§2–§3), and a shared/exposed agent carries structure + attestation but **never** identity or secrets (self-describing, not self-authorizing).

Pairs with the AI-governance view in [AGENT_OPS_EVAL_ARCHITECTURE.md](AGENT_OPS_EVAL_ARCHITECTURE.md) §6.4 (the shared substrate) — one key, three attested facts (trajectory · verdict · trust).

---

## 7. Audit Logging

### 7.1 JWT & Access Token Audit Trail

**JWT tokens** (`auth_tokens` table):
```sql
id           UUID PRIMARY KEY
user_id      TEXT REFERENCES users(id)
token_hash   VARCHAR(255) UNIQUE  -- SHA-256 of token
expires_at   DATETIME
created_at   DATETIME
ip_address   VARCHAR(45)          -- IPv6-compatible
user_agent   VARCHAR(500)
revoked      BOOLEAN DEFAULT FALSE
revoked_at   DATETIME
INDEX(user_id, revoked, expires_at)
```

**Access tokens** — per-use audit fields on `access_tokens` table: `last_used_at`, `use_count`, `last_ip_address` (updated on every successful authentication).

---

### 7.2 Failed Login & Account Lockout

Fields on the `users` table:
```sql
failed_login_attempts  INTEGER DEFAULT 0
locked_until           DATETIME (NULL if not locked)
last_failed_login_at   DATETIME
```

**Lockout thresholds** (configurable via environment variables):
- After `TDA_MAX_LOGIN_ATTEMPTS` (default: 5) consecutive failures → account locked for `TDA_LOCKOUT_DURATION_MINUTES` (default: 15 minutes)
- Progressive delay between attempts: `min(base * 2^(attempts-2), max_seconds)`
  - 1st failure: no delay; 2nd: 2s; 3rd: 4s; 4th: 8s; 5th: 16s → lockout

On successful login: `failed_login_attempts` reset to 0, `locked_until` cleared.

---

### 7.3 SSO Group Sync Audit Log

Every change to a user's tier or group membership via SSO is recorded — whether on login (automatic re-sync) or via manual admin action.

**`sso_sync_events` table:**
```sql
id           UUID PRIMARY KEY
user_uuid    TEXT REFERENCES users(id)
config_id    TEXT  -- SSO provider config ID (NULL for manual syncs)
config_type  TEXT  -- 'oidc' | 'saml'
sync_type    TEXT  -- 'login' | 'manual'
old_tier     TEXT  -- tier before this sync
new_tier     TEXT  -- tier after this sync
old_groups   TEXT  -- JSON array of groups before
new_groups   TEXT  -- JSON array of groups after
changed      INTEGER DEFAULT 0  -- 1 if tier or groups actually changed
synced_at    DATETIME DEFAULT (datetime('now'))
```

This table supports:
- Compliance reporting: who had what tier, when, and why
- Incident investigation: which IdP group change triggered a privilege change
- Access reviews: complete timeline of every user's group and tier changes

Admin endpoint: `GET /api/v1/auth/sso/users/<id>/sync-history`

---

### 7.4 General Audit Log

**`audit_logs` table:**
```sql
id          UUID PRIMARY KEY
user_id     TEXT (nullable — pre-auth events)
action      VARCHAR(50)  -- 'login', 'logout', 'configure', 'execute', etc.
resource    VARCHAR(255) -- endpoint or resource path
status      VARCHAR(20)  -- 'success' | 'failure'
ip_address  VARCHAR(45)
user_agent  VARCHAR(500)
details     TEXT         -- JSON for extensibility
metadata_json TEXT       -- the event's structured detail (log_audit_event's `metadata`)
timestamp   DATETIME
INDEX(user_id, timestamp)
INDEX(action, timestamp)
```

`metadata_json` (Oct 2026, added by a startup migration) is named so because SQLAlchemy reserves
`metadata` on every model: an `AuditLog(metadata=…)` keyword had been silently dropped, and
`GET /v1/auth/me/audit-logs` answered 500. The column is covered by the append-only triggers (§13).
`user_id` is `ON DELETE SET NULL`, so deleting an account keeps its audit trail. Gate:
`test/test_audit_integrity.py`.

---

## 8. Network Security

### 8.1 TLS/HTTPS

Uderia is designed to run behind a reverse proxy (nginx, Caddy, Traefik) that handles TLS termination. Direct TLS is not built into the application server.

**Production deployment requirements:**
- TLS 1.2 minimum, TLS 1.3 recommended
- HTTPS-only in production (redirect HTTP → HTTPS at proxy level)
- HSTS recommended for the proxy configuration

### 8.2 IP Address Handling

The application trusts proxy-set headers for client IP extraction (important for rate limiting accuracy):

1. `X-Forwarded-For` header — first IP in the chain (closest to client)
2. `X-Real-IP` header — direct proxy IP
3. `request.remote_addr` — direct connection fallback

**Important:** In production, restrict which IPs can set `X-Forwarded-For` to prevent IP spoofing by configuring your reverse proxy to overwrite this header.

### 8.3 Rate Limiting

IP-based token-bucket rate limiting on the authentication endpoints and on unauthenticated API calls. See [Section 3.4](#34-consumption-profiles--rate-limiting) for limits and configuration.

### 8.4 CORS

`main.cors_allowed_origins()` reads `CORS_ALLOWED_ORIGINS` — a comma-separated list of exact origins
(`https://app.example.com,https://portal.example.com`). Unset (or `*`) keeps the permissive default:
any origin may call the API cross-origin. That default is tolerable because the API authenticates
with a bearer token in the `Authorization` header, never an ambient cookie, so a foreign page holds
no credentials to ride on; a deployment that serves no cross-origin browser client should still set
the list.

---

## 9. Platform Connector Security

Platform connectors provide the AI agent with autonomous execution capabilities (browser, file system, web search, shell execution). Because these are high-privilege capabilities, they are governed by a strict three-layer architecture.

### 9.1 Namespace Separation

Platform connectors are **permanently separate** from user-configured MCP data source servers. They:
- Live in a different database table (`platform_connectors`)
- Are configured by admins, not users
- Are displayed in a separate UI panel (Components → Connectors, not Anatomy → MCP Servers)
- Never appear in the user-facing MCP server configuration panel

### 9.2 Three-Layer Governance Model

```
Admin Layer    →  installs connectors, sets available_tools, opt-in policy, credentials
     ↓
User Layer     →  toggles connectors on/off per profile (within admin bounds)
     ↓
Profile Layer  →  selects active tools per connector (within admin-permitted set)
```

**Admin governance fields on `platform_connectors`:**
```sql
enabled                  -- admin master switch
available_tools          -- JSON array: which tools are permitted
auto_opt_in              -- 1 = active on all profiles by default
user_can_opt_out         -- 1 = user can disable on their profile
user_can_configure_tools -- 1 = user can select individual tools
credentials              -- Fernet-encrypted admin API keys
```

### 9.3 Connector Type System

| Type | Transport | Use Case |
|---|---|---|
| `mcp_stdio` | Subprocess stdin/stdout (MCP wire protocol) | All current connectors |
| `mcp_http` | HTTP/SSE MCP endpoint | Remote/cloud-hosted connectors |
| `rest` | Direct REST API | Non-MCP tools — **future** (no invocation strategy registered) |
| `oauth_only` | Authentication only | **future** (no invocation strategy registered) |

Only `mcp_stdio` and `mcp_http` have an invocation strategy in `_INVOCATION_STRATEGIES`.

### 9.4 Shell Connector Security (`uderia-shell`)

The shell execution connector requires **two mandatory, independent security layers**:

**Layer 1 — Admin governance:** Controls who can access shell tools (via connector governance settings). Only explicitly enabled profiles can invoke shell tools.

**Layer 2 — Docker isolation:** Each shell tool invocation runs in a **fresh, throwaway Docker container**:
- No host filesystem access (only explicitly admin-configured mount paths)
- No network (`--network=none`)
- Resource limits: 1 CPU, 512MB RAM, 30-second execution timeout; read-only root filesystem with a 50MB writable `/tmp`
- Runs as user `nobody` inside the container
- Commands limited to the admin's allowlist (`ALLOWED_COMMANDS`)
- Container destroyed immediately after invocation completes

**Enable flow:** Admin must acknowledge a security disclaimer before the enable toggle appears:
> *"uderia-shell executes commands on the Uderia server inside an isolated Docker container. Docker must be installed on the host. Every execution is recorded in the platform audit log (who ran it, the command, the outcome, how long it took). By enabling this server you accept responsibility for access governance."*

**What is recorded:** every execution is written to the audit log by the Uderia process that calls the connector (`core/platform_connector_registry.invoke_connector_tool`, action `connector_tool_invocation`): the user, the command and other arguments (credential-like keys redacted, long values truncated), the outcome and the duration, for every platform connector. The connector process itself (`mcp_servers/builtin/uderia-shell/server.py`) persists nothing. The exit code and output also return to the calling turn, whose tool call is recorded in the session and signed into its execution provenance chain.

**Files:** `src/trusted_data_agent/core/platform_connector_registry.py`, `src/trusted_data_agent/connectors/registry.py`

---

## 10. Threat Model

| Threat | Mitigation |
|---|---|
| **Unrecorded prompt change** | Every stored change versioned in `prompt_versions` (who, why); a release never overwrites a person's edit; the editor gated by a signed Enterprise or Partner licence |
| **License forgery** | RSA-PSS 4096-bit signature cannot be forged without private key |
| **Execution log tampering** | Ed25519 hash chain — any modification breaks chain verification |
| **Step injection** | Chain linking (`previous_hash`) detects inserted steps |
| **Step removal** | Step count + chain gap detects removed steps |
| **Replay attack** | Timestamps + chain context prevent reuse of old provenance steps |
| **Cross-session tampering (Genie)** | Child chain references link sessions into a Merkle tree |
| **Brute-force login** | 5 failures → 15-minute lockout + exponential progressive delay |
| **Token compromise** | Per-token revocation in database; cache TTL 60s limits blast radius |
| **Credential leakage via API** | `[REDACTED]` sentinel — secrets never returned in API responses |
| **LLM API key theft** | Per-user Fernet encryption; keys never in memory longer than needed |
| **SSO client secret theft** | Fernet-encrypted storage; `[REDACTED]` in all API responses |
| **SAML assertion forgery** | `signxml` verifies IdP certificate signature on every assertion |
| **SAML replay attack** | `NotOnOrAfter` expiry check; `InResponseTo` must answer an AuthnRequest this SP issued; relay state consumed once; assertion-ID replay cache |
| **XML signature wrapping / XXE (SAML)** | Facts read only from the signed element; hardened parser refuses DOCTYPE/entities |
| **Stolen file-store object** | Per-file AES-256-GCM envelope encryption; data keys wrapped by the master-derived KEK or an external KMS (§4.5) |
| **Privilege escalation via SSO** | Group-to-tier re-evaluated on every login; all changes audit-logged |
| **Rate limit bypass** | Token bucket enforced server-side, keyed by IP with trusted proxy headers |
| **Shell code execution** | Two-layer isolation: admin governance + Docker container per invocation |
| **Man-in-the-middle (LLM calls)** | Content hashes detect response substitution in provenance chain |
| **Master key compromise** | `maintenance/rotate_encryption_key.py` re-encrypts every stored secret under a new key (server stopped, §13.1) |

---

## 11. Enterprise Compliance

| Regulation | Requirement | How Uderia Addresses It |
|---|---|---|
| **EU AI Act** | Transparency and traceability for AI systems | EPC records every LLM call, tool selection, and response with Ed25519-signed cryptographic proof |
| **GDPR Art. 22** | Right to explanation of automated decisions | Provenance chain traces each decision from query to final answer |
| **GDPR Art. 30** | Records of processing activities | Session-level provenance with cross-turn integrity; SSO sync event log |
| **SOX** | Audit trails for financial reporting | Tamper-evident record of every AI-assisted operation, offline-verifiable |
| **ISO 27001** | Information security management | Versioned prompts, signed execution logs, key management procedures, access controls |
| **HIPAA** | Audit controls for access to ePHI | JWT/access token audit trail; session isolation; consumption enforcement |
| **FedRAMP (guidance)** | Cryptographic key management | Documented key rotation procedures; Ed25519 for signing; RSA-4096 for licenses |

---

## 12. Performance Impact

| Operation | Latency | When |
|---|---|---|
| Prompt decryption | <10ms | First load; cached in memory thereafter |
| Bootstrap re-encryption | 2–3 seconds | One-time on first startup |
| Provenance step (hash + sign) | ~100 microseconds | Per execution step |
| Chain verification Level 1 | ~1ms | On-demand via REST API |
| License verification | ~5ms | Application startup |
| Auth cache hit | <1ms | JWT validated within 60-second TTL |
| Auth DB queries (cache miss) | 5–10ms | Revocation check + user load |
| Fernet encryption/decryption | <1ms | Credential access |
| bcrypt verify | 50–200ms | Login only (intentional — 12 rounds) |

**Total per-query overhead:** <2ms for provenance across typical 5–15 execution steps.

---

*This document covers the complete security architecture; last verified against code 2026-10-30. For implementation details and source references, see the files listed in each section.*

---

## 13. Hardened Mode and the Federal Hardening Fixes (Oct 2026)

Hardened mode is switched on with `TDA_SECURITY_PROFILE=hardened` (also `federal`/`cjis`) or `TDA_ENV=production`. Without it, every behaviour below that could stop a running deployment is a warning only, so existing installations are unaffected.

| Area | What changed | Hardened mode adds | Gate |
|---|---|---|---|
| SAML 2.0 | Attributes are read only from the element the signature covers (closes XML signature wrapping); Audience must name this SP; bearer SubjectConfirmation must name our ACS and answer an AuthnRequest we issued (RelayState is now consumed and bound); assertion IDs are single use; DOCTYPE/entities refused (XXE); Issuer and NotOnOrAfter required | — | `test/test_saml_hardening.py` |
| OIDC | Back-channel logout tokens are verified per OpenID Connect Back-Channel Logout 1.0 (JWKS signature, iss/aud, event, iat, jti replay, no nonce) and end the user's platform sessions; id_token validation fails closed without `python-jose`; PKCE (RFC 7636, S256) on every authorization | — | `test/test_oidc_hardening.py` |
| OAuth social login | PKCE for Google, GitHub, Microsoft, Okta; the state check no longer logs the session | — | `test/test_oidc_hardening.py` |
| Keys | One key policy (`core/key_policy.py`) replaces three copies of the default master key; the Ed25519 evidence key can be encrypted at rest | Refuses to start with the default or a short `TDA_ENCRYPTION_KEY`, or with an unencrypted on-host signing key (set the passphrase or use the HSM/KMS signer) | `test/test_key_policy.py` |
| Sessions | Browser idle lock on real user input, shared across tabs, one-minute warning; server-side idle expiry on token use (`auth_tokens.last_used_at`); while the user is active the lock sends a throttled `/api/v1/auth/me` heartbeat so the server's idle clock follows the browser's; a 401 that refused a **session** token (never a `tda_` access token) is stamped `X-Session-State: invalid` at one after-request seam (`auth.middleware.session_invalid_marker`), and one fetch guard in `auth.js` ends the tab once and sends the user to `/login?reason=session` | 30-minute default; a session token without a server record is refused | `test/test_session_idle.py`, `test/test_idle_lock.mjs` |
| Audit | Database triggers make `audit_logs`, `sso_sync_events`, `kg_lifecycle_audit` and `pack_lifecycle_audit` append-only within the retention period (≥ 365 days); deleting an account no longer deletes its audit trail; syslog / JSON-lines export for a SIEM; each event's structured detail is stored (`audit_logs.metadata_json`, §7.4) | — | `test/test_audit_integrity.py` |
| Resource catalog routes | The legacy `/tools`, `/prompts`, `/connectors`, `/resources` routes served the process-global catalog without a token; they now require one and serve `?profile_id=` or the caller's default agent | — | `test/test_resource_routes_auth.py` |
| Unauthenticated routes (Oct 2026 audit) | `GET /api_key/<provider>` returned the server's environment LLM keys without a token (now admin only); the notification stream trusted a `user_uuid` query parameter (now a one-minute signed ticket from `POST /api/notifications/ticket`, never accepted as a bearer); `cleanup-orphans` had no admin gate and judged orphans against the caller's sessions (now admin only, judged over every user's sessions, nothing deleted when the index is unreadable); the RAG collection refresh had no auth (now owner or admin); `POST /v1/configure` ran the service setup for an anonymous caller and broadcast "refresh" to every user (now 401 without a user, notice to the caller's pages only); the all-users session list flipped the process-wide session filter around an await (now a per-call scope, `test/test_session_scope_per_call.py`) | — | `test/test_unauth_routes.py` |
| Database integrity | An unreadable `tda_auth.db` is announced (CRITICAL log + an app banner on every open stream, `core/db_health.report_unreadable`); the daily `platform-db-backup` job (02:30 UTC) writes a `VACUUM INTO` copy verified by `quick_check` into `backups/db/`, keeping 7 — also the daily integrity read. Off-host copies should read `backups/db/`, never the live file | — | `test/test_db_health.py` |

Residual limits, stated plainly: there is still no built-in MFA (enforce it at the identity provider); cryptography is not FIPS 140-3 validated (build on a validated OpenSSL provider); TLS is terminated by the reverse proxy; whoever can run DDL on the database can drop a trigger, which is what the off-host SIEM copy is for; the SAML replay cache and OIDC logout `jti` cache are per process.

### 13.1 Turning hardened mode on (operator procedure)

`docker-compose.yml` forwards every setting below from the untracked `.env` beside it. Each is empty unless set, and the code treats an empty value as unset, so an instance that sets nothing behaves exactly as before.

**Never set `TDA_ENCRYPTION_KEY` by hand on an instance that already stores secrets.** The key derives the encryption keys for every stored secret, so changing it alone makes all of them unreadable. `maintenance/rotate_encryption_key.py` re-encrypts all of them in one transaction (nothing is written unless every secret decrypts with the old key) and then writes the new key to `.env` with mode 0600; the key is not printed. It covers the three key families: per-user credentials (`user_credentials`: LLM keys, database connections, per-user TTS), the platform salt (`platform_connectors.credentials`, `messaging_identities` access and refresh tokens, `sso_configurations.client_secret`, `saml_configurations.sp_private_key`) and the global TTS salt (`tts_global_config`). Gate: `test/test_rotate_encryption_key.py`, which encrypts with the platform's own modules, rotates, and decrypts with those modules under the new key.

The server also loads `.env` itself (`main.py` calls `load_dotenv()`), so an instance whose `.env` already holds a `TDA_ENCRYPTION_KEY` has been using it all along. Run the dry run first: if every secret decrypts with that key and it is not the development default, **no rotation is needed** — skip step 2 and only add the settings below. The script's default old key follows the server: the environment, then `.env`, then the development default.

On the production host (the repository is mounted at `/app`, so `.env` and `tda_auth.db` are the host's files):

```bash
cd /path/to/uderia        # the host checkout mounted at /app
cp tda_auth.db tda_auth.db.pre-hardening && cp .env .env.pre-hardening && cp -r tda_keys tda_keys.pre-hardening
sudo docker stop <container>

# 1. Every secret must decrypt with the current key. Writes nothing.
sudo docker compose run --rm --no-deps uderia python maintenance/rotate_encryption_key.py --dry-run
# 2. Re-encrypt everything under a new generated key; writes TDA_ENCRYPTION_KEY to .env.
sudo docker compose run --rm --no-deps uderia python maintenance/rotate_encryption_key.py
```

Then add to `.env` (generate each secret with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`):

```bash
TDA_PROVENANCE_KEY_PASSPHRASE=<generated>   # encrypts the Ed25519 evidence key at rest
TDA_SECURITY_PROFILE=hardened
# optional
TDA_SESSION_IDLE_MINUTES=30                 # hardened default is 30
TDA_AUDIT_RETENTION_DAYS=365                # minimum 365
TDA_AUDIT_SYSLOG=udp://<siem-host>:514      # or tcp://host:6514
TDA_AUDIT_JSONL=/app/logs/audit.jsonl
```

Store `TDA_ENCRYPTION_KEY` and `TDA_PROVENANCE_KEY_PASSPHRASE` in a password manager: losing the first makes every stored secret unreadable, losing the second makes the evidence-signing key unusable. Apply with `sudo docker compose up -d` (a plain `docker restart` does not re-read `.env` or the compose file).

Verify: the log shows `Key policy: hardened mode, no insecure key settings found.`; existing users can log in (sessions issued before the switch may need a fresh login, since hardened mode refuses a session token with no server record); a connector, SSO login or LLM call that uses a stored secret still works; the evidence key file is now encrypted (same public key, so existing signatures still verify). If the server refuses to start, the log names every open finding and the setting that clears it.

Rollback: stop the container, restore `tda_auth.db`, `.env` and `tda_keys` from the `.pre-hardening` copies, `sudo docker compose up -d`.
