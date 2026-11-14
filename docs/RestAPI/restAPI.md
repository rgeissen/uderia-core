# Uderia Platform REST API Documentation

## Table of Contents

1. [Introduction](#1-introduction)
2. [Authentication](#2-authentication)
   - [Access Tokens (Recommended)](#21-access-tokens-recommended)
   - [JWT Tokens](#22-jwt-tokens)
   - [Quick Start Guide](#23-quick-start-guide)
3. [API Endpoints](#3-api-endpoints)
   - [Authentication Endpoints](#31-authentication-endpoints)
     - [OAuth Authentication](#314-oauth-authentication)
   - [Access Token Management](#32-access-token-management)
   - [Application Configuration](#33-application-configuration)
   - [Session Management](#34-session-management)
   - [Query Execution](#35-query-execution)
   - [Task Management](#36-task-management)
   - [RAG Collection Management](#37-rag-collection-management)
   - [RAG Template System](#38-rag-template-system)
   - [MCP Server Management](#39-mcp-server-management)
   - [Profile Management](#310-profile-management)
   - [Session Analytics](#311-session-analytics-and-management)
   - [System Prompts Management](#312-system-prompts-management)
     - [Prompt Editor Endpoints](#3125-prompt-editor-endpoints)
   - [Document Upload](#313-document-upload)
   - [Agent Pack Management](#314-agent-pack-management)
   - [Knowledge Repository Management](#315-knowledge-repository-management)
   - [LLM Configuration Management](#316-llm-configuration-management)
   - [Consumption & Analytics](#317-consumption--analytics)
   - [Cost Management](#318-cost-management)
   - [Genie Multi-Profile Coordination](#319-genie-multi-profile-coordination)
   - [Extension Management](#320-extension-management)
   - [Skill Management](#321-skill-management)
   - [Admin Endpoints](#322-admin-endpoints) (incl. users, consumption, settings, operations, SSO/SAML)
   - [Component Architecture](#323-component-architecture)
   - [Context Window Management](#324-context-window-management)
   - [Prompt Execution](#325-prompt-execution)
   - [Vector Store Configuration Management](#326-vector-store-configuration-management)
   - [Execution Provenance & Audit Trail](#327-execution-provenance--audit-trail)
   - [Knowledge Graph Marketplace](#328-knowledge-graph-marketplace)
   - [Scheduled Tasks](#329-scheduled-tasks)
   - [Platform Connectors (OAuth)](#330-platform-connectors-oauth)
   - [Dashboard Canvas](#331-dashboard-canvas)
   - [Flow Execution & Monitoring](#332-flow-execution--monitoring)
   - [MCP Server (Uderia as MCP Provider)](#333-mcp-server--uderia-as-an-mcp-provider-tier-21)
   - [Agent Evaluation (Evals)](#334-agent-evaluation-evals)
   - [Knowledge Graph Management](#335-knowledge-graph-management)
   - [Database Connections](#336-database-connections)
   - [Platform Connectors & Connector Registry](#337-platform-connectors--connector-registry)
   - [Component Instances & Knowledge Repositories](#338-component-instances--knowledge-repositories)
   - [Trust Membrane](#339-trust-membrane-tier-52)
     - [Assurance Bindings & the Module Plane](#339a-assurance-bindings--the-module-plane-prop-003)
     - [Withdrawing — the inverses](#339b-w-withdrawing--the-inverses-oct-2026)
     - [Workload Tuning](#339c-workload-tuning-prop-008-c1)
   - [Governed Metric Semantic API for BI](#340-governed-metric-semantic-api-for-bi-tier-42--t3)
     - [Database Navigator](#340a-database-navigator)
   - [The Vote Signal](#341-the-vote-signal-per-turn-feedback)
   - [OBDA-READ Data Products](#342-obda-read-data-products-tier-55)
     - [Signed Actions (OBDA-ACT)](#342a-signed-actions-prop-008-obda-act)
   - [Schema Ledger (admin)](#343-schema-ledger-admin)
   - [Files and File Stores](#344-files-and-file-stores-prop-010)
   - [Agent Memory](#345-agent-memory)
   - [Marketplace — Collections, Extensions, Component Instances, Sharing](#346-marketplace--collections-extensions-component-instances-sharing)
   - [Additional Endpoint Reference](#347-additional-endpoint-reference)
   - [Web-UI Internal Routes](#348-web-ui-internal-routes)
4. [Data Models](#4-data-models)
5. [Code Examples](#5-code-examples)
6. [Security Best Practices](#6-security-best-practices)
7. [Troubleshooting](#7-troubleshooting)
8. [Quick Reference](#8-quick-reference)
9. [API Updates & Migration Notes](#9-api-updates--migration-notes)
10. [Additional Resources](#10-additional-resources)

---

## 1. Introduction

Welcome to the Uderia Platform (TDA) REST API. This API provides a programmatic interface to interact with the agent's powerful data analysis and querying capabilities.

The API is designed around an **asynchronous task-based architecture**. This pattern is ideal for handling potentially long-running agent processes in a robust and scalable way. Instead of holding a connection open while the agent works, you initiate a task and then poll a status endpoint to get progress updates and the final result. You can also cancel a running task if needed.

### Key Features

- **🔐 Secure Authentication**: Dual authentication system with JWT and long-lived access tokens
- **⚡ Asynchronous Query Execution**: Submit queries and poll for results without holding connections
- **🧠 RAG Collection Management**: Create and manage collections of query patterns for context-aware responses
- **📝 Template-Based Population**: Use modular templates to automatically generate RAG case studies
- **🤖 LLM-Assisted Generation**: Generate question/SQL pairs automatically from database schemas
- **🔌 MCP Server Integration**: Connect to multiple Model Context Protocol servers for data access
- **💬 Session Management**: Maintain conversation context across multiple queries
- **📊 Analytics & Monitoring**: Track token usage, costs, and performance metrics

### Base URL

All API endpoints are relative to your TDA instance:
```
http://your-tda-host:5050/api
```

For local development:
```
http://localhost:5050/api
http://127.0.0.1:5050/api
```

### Prerequisites for REST API

⚠️ **Important:** Before you can use REST endpoints for session creation or query execution, you must:

1. **Authenticate** - Login and obtain an access token (JWT or long-lived token)
2. **Configure Profile** - Create a profile that combines:
   - An **LLM Provider** (Google, Anthropic, Azure, AWS, Friendli, OpenRouter, xAI, MLX, or Ollama)
   - An **MCP Server** (your data source and tools)
3. **Set as Default** - Mark your profile as the default for your user account

Without a configured default profile, REST endpoints will return `400 Bad Request` with a helpful error message.

### Quick Start Workflow

The typical REST API workflow involves five steps:

**1. Authenticate** - Obtain a JWT from `/api/v1/auth/login`, then create long-lived token via `/api/v1/auth/tokens`  
**2. Create Profile** (One-time) - Configure LLM + MCP via UI or REST endpoints, set as default  
**3. Create a Session** - `POST /api/v1/sessions` to start a conversation context  
**4. Submit a Query** - `POST /api/v1/sessions/{session_id}/query` with your prompt  
**5. Poll for Results** - `GET /api/v1/tasks/{task_id}` to check status and retrieve results  

```bash
# 1. Login to get JWT (one-time or per session)
JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your_password"}' | jq -r '.access_token')

# 2. Create access token for API automation (one-time)
TOKEN=$(curl -s -X POST http://localhost:5050/api/v1/auth/tokens \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"name":"my_app","expires_in_days":90}' | jq -r '.token')

# Note: Configure Profile in UI or API (set LLM + MCP, mark as default)

# 3. Create session (uses your default profile)
SESSION=$(curl -s -X POST http://localhost:5050/api/v1/sessions \
  -H "Authorization: Bearer $TOKEN")
SESSION_ID=$(echo $SESSION | jq -r '.session_id')

# 4. Submit query
TASK=$(curl -s -X POST http://localhost:5050/api/v1/sessions/$SESSION_ID/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Show me available databases"}')
TASK_ID=$(echo $TASK | jq -r '.task_id')

# 5. Check result
curl -s http://localhost:5050/api/v1/tasks/$TASK_ID \
  -H "Authorization: Bearer $TOKEN" | jq '.result'
```

---

## 2. Authentication

The Uderia Platform REST API requires authentication for all endpoints except public registration. We support two authentication methods optimized for different use cases.

### 2.1. Access Tokens (Recommended)

**Best for:** REST API clients, automation scripts, CI/CD pipelines, external integrations

Access tokens are **long-lived API keys** that provide secure programmatic access without exposing credentials.

#### Features

✅ **Long-lived** - Configurable expiration (30/60/90/180/365 days) or never expires  
✅ **Secure** - SHA256 hashed storage, shown only once on creation  
✅ **Trackable** - Monitor usage count and last used timestamp  
✅ **Revocable** - Instantly revoke compromised tokens  
✅ **Multiple tokens** - Create separate tokens per application/environment  
✅ **Named** - Descriptive names for easy management (e.g., "Production Server")

#### Token Format

```
tda_<32-random-characters>
```

Example: `tda_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p`

#### Using Access Tokens

Include the token in the `Authorization` header with the `Bearer` scheme:

```bash
curl -X GET http://localhost:5050/api/v1/sessions \
  -H "Authorization: Bearer tda_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"
```

### 2.2. JWT Tokens

**Best for:** Web UI sessions, interactive applications, short-term access

JWT (JSON Web Tokens) are short-lived session tokens automatically managed by the web interface.

#### Features

✅ **Auto-expiration** - 24-hour lifetime  
✅ **Stateless** - No server-side session storage  
✅ **Automatic** - Managed by web UI  

#### Using JWT Tokens

```bash
# Login to get JWT token
JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"your_username","password":"your_password"}' \
  | jq -r '.token')

# Use JWT token in API calls
curl -X GET http://localhost:5050/api/v1/sessions \
  -H "Authorization: Bearer $JWT"
```

### 2.3. Quick Start Guide

#### Step 1: Register an Account

```bash
curl -X POST http://localhost:5050/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "api_user",
    "email": "user@example.com",
    "password": "SecurePassword123!"
  }'
```

**Response:**
```json
{
  "status": "success",
  "message": "User registered successfully",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "api_user",
    "email": "user@example.com"
  }
}
```

#### Step 2: Login to Get JWT Token

```bash
curl -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "api_user",
    "password": "SecurePassword123!"
  }'
```

**Response:**
```json
{
  "status": "success",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "api_user",
    "email": "user@example.com",
    "user_uuid": "api_user_550e8400"
  }
}
```

#### Step 3: Create an Access Token

```bash
JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

curl -X POST http://localhost:5050/api/v1/auth/tokens \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production Server",
    "expires_in_days": 90
  }'
```

**Response:**
```json
{
  "status": "success",
  "token": "tda_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p",
  "token_id": "abc123-def456-ghi789",
  "name": "Production Server",
  "created_at": "2025-11-25T10:00:00Z",
  "expires_at": "2026-02-25T10:00:00Z"
}
```

⚠️ **CRITICAL:** Copy the `token` value immediately! It cannot be retrieved later.

#### Step 4: Use Your Access Token

```bash
TOKEN="tda_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"

curl -X POST http://localhost:5050/api/v1/sessions \
  -H "Authorization: Bearer $TOKEN"
```

---

## 3. API Endpoints

The base URL for all endpoints is `/api`.

### 3.1. Authentication Endpoints

#### 3.1.1. Register User

Create a new user account.

**Endpoint:** `POST /api/v1/auth/register`  
**Authentication:** None (public endpoint)

**Request Body:**
```json
{
  "username": "api_user",
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```

**Validation Rules:**
- `username`: 3-50 characters, alphanumeric and underscores only
- `email`: Valid email format
- `password`: Minimum 8 characters

**Success Response:**
```json
{
  "status": "success",
  "message": "User registered successfully",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "api_user",
    "email": "user@example.com"
  }
}
```

**Error Responses:**
- `400 Bad Request` - Validation failed or username/email already exists
- `429 Too Many Requests` - Rate limit exceeded (3 registrations per hour per IP)

#### 3.1.2. Login

Authenticate and receive a JWT token.

**Endpoint:** `POST /api/v1/auth/login`  
**Authentication:** None (uses credentials)

**Request Body:**
```json
{
  "username": "api_user",
  "password": "SecurePassword123!"
}
```

**Success Response:**
```json
{
  "status": "success",
  "message": "Login successful",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "api_user",
    "user_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "display_name": "API User",
    "email": "user@example.com",
    "is_admin": false
  }
}
```

`user.user_uuid` is the user's id (the same value as `user.id`). A successful login also bootstraps the user's default profile, default planner collection, built-in extensions and built-in skills on first use.

**Error Responses:**
- `400 Bad Request` - Missing username or password
- `401 Unauthorized` - Invalid username or password, or account locked. After repeated failures the body adds `failed_attempts`, `remaining_attempts`, and `retry_after_seconds` (wait before the next try) or `locked: true` + `retry_after_minutes`
- `429 Too Many Requests` - Per-IP login rate limit exceeded; body carries `retry_after`. Obtain one token and reuse it — never log in inside a polling loop

**Token Lifetime:** 24 hours

#### 3.1.3. Logout

Invalidate the current JWT token (web UI only - access tokens should be revoked via API).

**Endpoint:** `POST /api/v1/auth/logout`  
**Authentication:** Required (JWT token)

**Success Response:**
```json
{
  "status": "success",
  "message": "Logout successful"
}
```

#### 3.1.4. OAuth Authentication

The platform supports OAuth 2.0 authentication with Google and GitHub providers. OAuth provides a secure, password-less login experience and allows users to link multiple authentication methods to a single account.

**Supported Providers:**
- Google OAuth 2.0
- GitHub OAuth 2.0

**OAuth Flow:**
1. User initiates OAuth login
2. Platform redirects to provider (Google/GitHub)
3. User authenticates with provider
4. Provider redirects back with authorization code
5. Platform exchanges code for access token
6. Platform creates or links user account
7. JWT token issued for platform access

---

##### Get Available OAuth Providers

Retrieve list of configured OAuth providers.

**Endpoint:** `GET /api/v1/auth/oauth/providers`
**Authentication:** None (public endpoint)

**Success Response:**
```json
{
  "status": "success",
  "providers": [
    {
      "name": "google",
      "display_name": "Google",
      "icon": "oauth-google",
      "enabled": true
    },
    {
      "name": "github",
      "display_name": "GitHub",
      "icon": "oauth-github",
      "enabled": true
    }
  ]
}
```

**Use Case:** Display OAuth login buttons in UI

---

##### Initiate OAuth Login Flow

Redirect user to OAuth provider authorization endpoint.

**Endpoint:** `GET /api/v1/auth/oauth/<provider>`
**Authentication:** None (public endpoint)

**Path Parameters:**
- `provider` (string, required): OAuth provider (`google` or `github`)

**Query Parameters:**
- `return_to` (string, optional): URL to redirect to after successful authentication

**Response:**
- **Redirect** (302): Redirects to OAuth provider authorization page
- **Error** (404): Provider not configured
- **Error** (500): Failed to build authorization URL

**Example:**
```bash
# Initiate Google OAuth login
curl -L "http://localhost:5050/api/v1/auth/oauth/google?return_to=/dashboard"

# User is redirected to Google login page
# After authentication, redirected back to platform callback
```

**Workflow:**
1. User clicks "Login with Google" button
2. Frontend redirects to `/api/v1/auth/oauth/google`
3. Backend generates OAuth state token (CSRF protection)
4. Backend redirects to Google authorization page
5. User authorizes application
6. Google redirects to callback endpoint with authorization code

---

##### OAuth Callback Handler

Handle OAuth callback from provider after user authorization.

**Endpoint:** `GET /api/v1/auth/oauth/<provider>/callback`
**Authentication:** None (public endpoint, validated via OAuth state)

**Path Parameters:**
- `provider` (string, required): OAuth provider (`google` or `github`)

**Query Parameters:**
- `code` (string, required): Authorization code from provider
- `state` (string, required): State parameter for CSRF protection
- `error` (string, optional): Error code if authorization failed

**Success Response:**
- **Redirect** (302): Redirects to application with JWT token in URL fragment or cookie
- **Response Body** (if API mode):
```json
{
  "status": "success",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {
    "uuid": "123e4567-e89b-12d3-a456-426614174000",
    "username": "user@gmail.com",
    "email": "user@gmail.com",
    "tier": "user"
  },
  "new_account": false
}
```

**Error Responses:**
```json
// Invalid OAuth state (CSRF protection)
{
  "status": "error",
  "message": "Invalid OAuth state parameter"
}

// Authorization denied by user
{
  "status": "error",
  "message": "OAuth authorization was denied"
}

// Failed to exchange code for token
{
  "status": "error",
  "message": "Failed to exchange authorization code"
}
```

**Workflow:**
1. Provider redirects to `/oauth/<provider>/callback?code=...&state=...`
2. Backend validates state parameter
3. Backend exchanges authorization code for access token
4. Backend retrieves user profile from provider
5. Backend creates new user OR links to existing account
6. Backend issues JWT token
7. User redirected to application

---

##### Link OAuth Account

Link an OAuth provider to an existing authenticated user account.

**Endpoint:** `GET /api/v1/auth/oauth/<provider>/link`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `provider` (string, required): OAuth provider to link (`google` or `github`)

**Query Parameters:**
- `return_to` (string, optional): URL to redirect to after successful linking

**Response:**
- **Redirect** (302): Redirects to OAuth provider authorization page

**Example:**
```bash
# Link Google account to current user
curl -L "http://localhost:5050/api/v1/auth/oauth/google/link" \
  -H "Authorization: Bearer $JWT"

# User redirected to Google authorization page
# After authorization, OAuth account linked to current user
```

**Use Case:** User logged in with password wants to add Google login option

---

##### OAuth Link Callback

Handle OAuth callback for account linking.

**Endpoint:** `GET /api/v1/auth/oauth/<provider>/link/callback`
**Authentication:** Validated via OAuth state (contains user UUID)

**Path Parameters:**
- `provider` (string, required): OAuth provider (`google` or `github`)

**Query Parameters:**
- `code` (string, required): Authorization code from provider
- `state` (string, required): State parameter containing user UUID
- `error` (string, optional): Error code if authorization failed

**Success Response:**
```json
{
  "status": "success",
  "message": "OAuth account linked successfully",
  "provider": "google",
  "linked_email": "user@gmail.com"
}
```

**Error Responses:**
```json
// OAuth account already linked to another user
{
  "status": "error",
  "message": "This Google account is already linked to another user"
}

// OAuth account already linked to current user
{
  "status": "error",
  "message": "This Google account is already linked to your account"
}
```

**Client Behavior:**
- After successful linking, user can login with either password OR OAuth
- Multiple OAuth providers can be linked to same account
- User shown success message: "Google account linked successfully"

---

##### Disconnect OAuth Account

Remove OAuth provider link from user account.

**Endpoint:** `POST /api/v1/auth/oauth/<provider>/disconnect`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `provider` (string, required): OAuth provider to disconnect (`google` or `github`)

**Success Response:**
```json
{
  "status": "success",
  "message": "OAuth account disconnected successfully",
  "provider": "google"
}
```

**Error Responses:**
```json
// OAuth account not linked
{
  "status": "error",
  "message": "No Google account is linked to your account"
}

// Cannot disconnect last authentication method
{
  "status": "error",
  "message": "Cannot disconnect last authentication method. Please set a password first."
}
```

**Safety Rules:**
- User must have at least one authentication method (password OR OAuth)
- If user has only OAuth and no password, disconnect is blocked
- User shown warning: "Set password before disconnecting OAuth"

---

##### Get Linked OAuth Accounts

Retrieve all OAuth accounts linked to current user.

**Endpoint:** `GET /api/v1/auth/oauth/accounts`
**Authentication:** Required (JWT or access token)

**Success Response:**
```json
{
  "status": "success",
  "oauth_accounts": [
    {
      "provider": "google",
      "email": "user@gmail.com",
      "linked_at": "2025-11-25T10:00:00Z",
      "last_used": "2026-02-05T14:30:00Z"
    },
    {
      "provider": "github",
      "email": "user@users.noreply.github.com",
      "linked_at": "2025-12-01T12:00:00Z",
      "last_used": null
    }
  ],
  "has_password": true
}
```

**Response Fields:**
- `provider`: OAuth provider name (`google`, `github`)
- `email`: Email address from OAuth provider
- `linked_at`: Timestamp when OAuth account was linked
- `last_used`: Last login via this OAuth provider (null if never used)
- `has_password`: Whether user has password set (for disconnect safety)

**Use Case:** Display linked accounts in user profile settings

---

**OAuth Configuration:**

OAuth providers must be configured in `tda_config.json` or environment variables:

```json
{
  "oauth": {
    "google": {
      "client_id": "your-google-client-id.apps.googleusercontent.com",
      "client_secret": "your-google-client-secret",
      "redirect_uri": "http://localhost:5050/api/v1/auth/oauth/google/callback"
    },
    "github": {
      "client_id": "your-github-client-id",
      "client_secret": "your-github-client-secret",
      "redirect_uri": "http://localhost:5050/api/v1/auth/oauth/github/callback"
    }
  }
}
```

**Environment Variables (Alternative):**
```bash
GOOGLE_OAUTH_CLIENT_ID=your-google-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-google-client-secret
GITHUB_OAUTH_CLIENT_ID=your-github-client-id
GITHUB_OAUTH_CLIENT_SECRET=your-github-client-secret
```

**Setup Guide:** See `docs/OAuth/OAUTH.md` for detailed OAuth provider configuration.

---

#### 3.1.5. Verify Email Address

Verify a user's email address using a verification token sent via email.

**Endpoint:** `POST /api/v1/auth/verify-email`
**Authentication:** None (public endpoint, validated via token)

**Request Body:**
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "email": "user@example.com"
}
```

**Parameters:**
- `token` (string, required): Verification token from email
- `email` (string, required): Email address to verify

**Success Response:**
```json
{
  "status": "success",
  "message": "Email verified successfully",
  "user_id": 123
}
```

**Error Responses:**
```json
// Invalid or expired token
{
  "status": "error",
  "message": "Invalid or expired verification token"
}

// User not found
{
  "status": "error",
  "message": "User not found"
}
```

**Workflow:**
1. User registers account
2. System sends verification email with token
3. User clicks link in email: `https://app.com/verify?token=...&email=...`
4. Frontend calls this endpoint with token and email
5. Backend verifies token and marks email as verified
6. User can now login

**Client Behavior:**
- Show loading indicator: "Verifying email..."
- Success: Redirect to login page with message "Email verified! Please login."
- Failure: Show error with "Resend Verification Email" button

---

#### 3.1.6. Resend Verification Email

Resend email verification email to a user.

**Endpoint:** `POST /api/v1/auth/resend-verification-email`
**Authentication:** None (public endpoint)

**Request Body:**
```json
{
  "email": "user@example.com"
}
```

**Parameters:**
- `email` (string, required): Email address to resend verification to

**Success Response:**
```json
{
  "status": "success",
  "message": "Verification email sent successfully"
}
```

**Error Responses:**
```json
// Email already verified
{
  "status": "error",
  "message": "Email is already verified"
}

// User not found
{
  "status": "error",
  "message": "No user found with that email address"
}

// Rate limit exceeded
{
  "status": "error",
  "message": "Verification email already sent recently. Please wait 5 minutes."
}
```

**Rate Limiting:**
- Maximum 1 email per 5 minutes per email address
- Prevents abuse/spam

**Client Behavior:**
- Show success message: "Verification email sent. Check your inbox."
- Disable button for 5 minutes after sending
- Show countdown: "Resend available in 4:32"

---

#### 3.1.7. Forgot Password (Initiate Reset)

Initiate password reset flow by sending a reset token to user's email.

**Endpoint:** `POST /api/v1/auth/forgot-password`
**Authentication:** None (public endpoint)

**Request Body:**
```json
{
  "email": "user@example.com"
}
```

**Parameters:**
- `email` (string, required): Email address of account to reset

**Success Response:**
```json
{
  "status": "success",
  "message": "Password reset email sent if account exists"
}
```

**Security Note:**
- Always returns success even if email doesn't exist (prevents user enumeration)
- Only sends email if account exists
- Token expires after 1 hour

**Client Behavior:**
- Show generic success message: "If an account exists with that email, you will receive a password reset link."
- Do NOT reveal whether email exists or not (security)

---

#### 3.1.8. Reset Password (Complete Reset)

Complete password reset using token from email.

**Endpoint:** `POST /api/v1/auth/reset-password`
**Authentication:** None (public endpoint, validated via token)

**Request Body:**
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "email": "user@example.com",
  "new_password": "NewSecurePassword123!"
}
```

**Parameters:**
- `token` (string, required): Reset token from email
- `email` (string, required): Email address of account
- `new_password` (string, required): New password (minimum 8 characters)

**Password Requirements:**
- Minimum 8 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one number
- At least one special character (recommended but not enforced)

**Success Response:**
```json
{
  "status": "success",
  "message": "Password reset successfully"
}
```

**Error Responses:**
```json
// Invalid or expired token
{
  "status": "error",
  "message": "Invalid or expired reset token"
}

// Weak password
{
  "status": "error",
  "message": "Password does not meet requirements"
}
```

**Workflow:**
1. User clicks "Forgot Password"
2. Enters email, receives reset token via email
3. Clicks link in email: `https://app.com/reset-password?token=...&email=...`
4. Enters new password
5. Frontend calls this endpoint
6. Backend validates token and updates password
7. User redirected to login page

**Client Behavior:**
- After successful reset, show: "Password reset successfully. Please login with your new password."
- Automatically redirect to login page after 3 seconds

---

#### 3.1.9. Get Current User Profile

Retrieve profile information for the currently authenticated user.

**Endpoint:** `GET /api/v1/auth/me`
**Authentication:** Required (JWT or access token)

**Success Response:**
```json
{
  "status": "success",
  "user": {
    "uuid": "123e4567-e89b-12d3-a456-426614174000",
    "username": "john_doe",
    "email": "john@example.com",
    "email_verified": true,
    "tier": "developer",
    "created_at": "2025-11-25T10:00:00Z",
    "last_login": "2026-02-06T14:30:00Z",
    "profile_picture": null,
    "settings": {
      "theme": "dark",
      "notifications_enabled": true
    }
  }
}
```

**Response Fields:**
- `uuid`: Unique user identifier (used in API calls)
- `username`: Display name
- `email`: Account email address
- `email_verified`: Email verification status
- `tier`: Access tier (`user`, `developer`, `admin`, `enterprise`)
- `profile_picture`: URL to profile picture (null if not set)
- `settings`: User preferences

**Use Case:** Display user information in profile settings page

---

#### 3.1.10. Update Current User Profile

Update profile information for the currently authenticated user.

**Endpoint:** `PUT /api/v1/auth/me`
**Authentication:** Required (JWT or access token)

**Request Body (Partial Update):**
```json
{
  "username": "john_doe_updated",
  "email": "newemail@example.com",
  "settings": {
    "theme": "light",
    "notifications_enabled": false
  }
}
```

**Updatable Fields:**
- `username` (string, optional): New display name (3-50 characters)
- `email` (string, optional): New email address (triggers re-verification)
- `settings` (object, optional): User preferences
- `profile_picture` (string, optional): URL to new profile picture

**Success Response:**
```json
{
  "status": "success",
  "message": "Profile updated successfully",
  "user": {
    "uuid": "123e4567-e89b-12d3-a456-426614174000",
    "username": "john_doe_updated",
    "email": "newemail@example.com",
    "email_verified": false,
    "tier": "developer",
    "updated_at": "2026-02-06T14:35:00Z"
  }
}
```

**Email Change Behavior:**
- If email changed, `email_verified` set to `false`
- New verification email sent to new address
- Old email address remains active until new email verified

**Error Responses:**
```json
// Username already taken
{
  "status": "error",
  "message": "Username is already taken"
}

// Invalid email format
{
  "status": "error",
  "message": "Invalid email address"
}
```

---

#### 3.1.11. Get User Features

Retrieve available features for the current user's tier.

**Endpoint:** `GET /api/v1/auth/me/features`
**Authentication:** Required (JWT or access token)

**Success Response:**
```json
{
  "status": "success",
  "tier": "developer",
  "features": {
    "max_sessions": 10,
    "max_profiles": 20,
    "max_collections": 50,
    "max_prompts_per_month": 2000,
    "can_create_agent_packs": true,
    "can_publish_marketplace": false,
    "can_access_system_prompts": false,
    "can_use_oauth": true,
    "can_upload_documents": true,
    "document_upload_max_size_mb": 50,
    "max_tokens_per_query": 100000,
    "advanced_analytics": true,
    "custom_branding": false,
    "priority_support": false
  }
}
```

**Feature Matrix by Tier:**

| Feature | Free | User | Developer | Enterprise |
|---------|------|------|-----------|------------|
| Max Sessions | 3 | 5 | 10 | 50 |
| Max Profiles | 5 | 10 | 20 | ∞ |
| Max Collections | 10 | 25 | 50 | ∞ |
| Prompts/Month | 100 | 500 | 2000 | 10000 |
| Create Agent Packs | ❌ | ✅ | ✅ | ✅ |
| Publish Marketplace | ❌ | ❌ | ❌ | ✅ |
| System Prompts Access | ❌ | ❌ | ✅ | ✅ |
| Document Upload | ❌ | ✅ | ✅ | ✅ |
| Max Upload Size (MB) | - | 25 | 50 | 100 |

**Use Case:** Enable/disable UI features based on user tier

---

#### 3.1.12. Get User Panes

Retrieve the sidebar views (panes) and their per-tier visibility, for the caller to apply to its own tier.

**Endpoint:** `GET /api/v1/auth/me/panes`
**Authentication:** Required (JWT or access token)

**Success Response:**
```json
{
  "status": "success",
  "user_tier": "developer",
  "panes": [
    {
      "pane_id": "conversation",
      "pane_name": "Reasoning",
      "group": null,
      "description": "Conversations with your agents",
      "display_order": 1,
      "visible_to_user": true,
      "visible_to_developer": true,
      "visible_to_admin": true,
      "id": "…", "created_at": "…", "updated_at": "…"
    },
    {
      "pane_id": "credentials",
      "pane_name": "Anatomy",
      "group": "The Agent",
      "description": "Agent profiles, LLMs, MCP servers, stores, memory, skills and packs",
      "display_order": 2,
      "visible_to_user": true,
      "visible_to_developer": true,
      "visible_to_admin": true
    }
  ]
}
```

The list is in **sidebar order**. `pane_name`, `group`, `description` and `display_order` come from the pane catalogue (`core/pane_catalog.py`, which mirrors the sidebar) and are written onto the stored rows on every read; only the three `visible_to_*` flags are the administrator's choice. `pane_id` is a stable key — it never changes when a view is renamed.

**The panes and their defaults:**

| `pane_id` | Name | Sidebar group | User | Developer | Admin |
|---|---|---|---|---|---|
| `conversation` | Reasoning | — | ✅ | ✅ | ✅ |
| `credentials` | Anatomy | The Agent | ✅ | ✅ | ✅ |
| `agents` | Trust | The Agent | ✅ | ✅ | ✅ |
| `executions` | Awareness | The Agent | ❌ | ✅ | ✅ |
| `ontology` | Ontology *(only when the enterprise Ontology suite is present)* | The Agent | ✅ | ✅ | ✅ |
| `rag-maintenance` | Intelligence | Faculties | ❌ | ✅ | ✅ |
| `components` | Components | Faculties | ❌ | ✅ | ✅ |
| `marketplace` | Marketplace | Exchange | ✅ | ✅ | ✅ |
| `admin` | Administration | Platform | ❌ | ❌ | ✅ *(always)* |

**Client Behavior:**
- On app load, fetch panes and hide each sidebar view whose `visible_to_<tier>` is `false`
- Hide a sidebar group label when every view under it is hidden

---

#### 3.1.12a. Pane Visibility (Admin)

Administer which user tiers see each sidebar view (UI: **Administration → Panes**). All three require an admin token.

**List:** `GET /api/v1/admin/panes` — same `panes` array as §3.1.12 (sidebar order, catalogue names, `group`).

**Update one pane:** `PATCH /api/v1/admin/panes/<pane_id>/visibility`
```json
{ "visible_to_user": false, "visible_to_developer": true }
```
Any subset of `visible_to_user` / `visible_to_developer` / `visible_to_admin`. Returns `{"status": "success", "pane": {…}}`; `404` for an unknown `pane_id`. `visible_to_admin` on `admin` is always forced back to `true`. Audited as `pane_visibility_updated`.

**Reset to defaults:** `POST /api/v1/admin/panes/reset` — drops every stored row and recreates the catalogue with the default visibility in the table above. Returns `{"status": "success", "message": "…", "panes": […]}`. Audited as `panes_reset`.

```bash
curl -X PATCH http://localhost:5050/api/v1/admin/panes/executions/visibility \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"visible_to_user": true}'
```

---

#### 3.1.13. Change Password

Change password for the currently authenticated user.

**Endpoint:** `POST /api/v1/auth/change-password`
**Authentication:** Required (JWT or access token)

**Request Body:**
```json
{
  "current_password": "CurrentPassword123!",
  "new_password": "NewSecurePassword456!"
}
```

**Parameters:**
- `current_password` (string, required): User's current password for verification
- `new_password` (string, required): New password (minimum 8 characters)

**Success Response:**
```json
{
  "status": "success",
  "message": "Password changed successfully"
}
```

**Error Responses:**
```json
// Incorrect current password
{
  "status": "error",
  "message": "Current password is incorrect"
}

// Weak new password
{
  "status": "error",
  "message": "New password does not meet requirements"
}

// Same password
{
  "status": "error",
  "message": "New password must be different from current password"
}
```

**Client Behavior:**
- After successful change, show: "Password changed successfully"
- Optionally: Log out user and require re-login with new password
- Security best practice: Log out all sessions except current

---

#### 3.1.14. Refresh JWT Token

Refresh an expired or soon-to-expire JWT token.

**Endpoint:** `POST /api/v1/auth/refresh`
**Authentication:** Required (JWT token, even if expired within grace period)

**Request Body:**
```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Parameters:**
- `refresh_token` (string, optional): Refresh token (if using refresh token flow)
- OR use expired JWT in Authorization header (if within grace period)

**Success Response:**
```json
{
  "status": "success",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 86400,
  "expires_at": "2026-02-07T14:30:00Z"
}
```

**Token Refresh Flow:**

**Option 1: Grace Period (Recommended)**
- JWT tokens have 24-hour expiry
- 5-minute grace period after expiry
- Within grace period, can refresh with expired token

**Option 2: Refresh Tokens**
- Long-lived refresh token (30 days)
- Exchange refresh token for new access token
- Refresh token rotated on each use

**Error Responses:**
```json
// Token expired beyond grace period
{
  "status": "error",
  "message": "Token expired. Please login again."
}

// Invalid refresh token
{
  "status": "error",
  "message": "Invalid refresh token"
}
```

**Client Behavior:**
- **Automatic refresh:** Before JWT expires (e.g., at 23 hours), call refresh endpoint
- **On 401 errors:** Try refresh once, then redirect to login if refresh fails
- **Store new token:** Replace old JWT with new token in local storage

**Implementation Example:**
```javascript
// Automatic token refresh (30 minutes before expiry)
setInterval(async () => {
  const token = localStorage.getItem('jwt');
  const expiresAt = parseJWT(token).exp;
  const now = Date.now() / 1000;

  // Refresh if token expires in less than 30 minutes
  if (expiresAt - now < 1800) {
    const newToken = await refreshToken(token);
    localStorage.setItem('jwt', newToken);
  }
}, 60000); // Check every minute
```

---

### 3.2. Access Token Management

#### 3.2.1. Create Access Token

Generate a new long-lived access token for API authentication.

**Endpoint:** `POST /api/v1/auth/tokens`  
**Authentication:** Required (JWT or access token)

**Request Body:**
```json
{
  "name": "Production Server",
  "expires_in_days": 90
}
```

**Parameters:**
- `name` (string, required): Descriptive name for the token (3-100 characters)
- `expires_in_days` (integer, optional): Expiration in days (30/60/90/180/365) or `null` for never

**Success Response:**
```json
{
  "status": "success",
  "token": "tda_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p",
  "token_id": "abc123-def456-ghi789",
  "name": "Production Server",
  "created_at": "2025-11-25T10:00:00Z",
  "expires_at": "2026-02-25T10:00:00Z"
}
```

⚠️ **Security Note:** The full token is shown **only once**. Store it securely immediately.

**Error Responses:**
- `400 Bad Request` - Invalid parameters
- `401 Unauthorized` - Authentication required

#### 3.2.2. List Access Tokens

Retrieve all access tokens for the authenticated user.

**Endpoint:** `GET /api/v1/auth/tokens`  
**Authentication:** Required (JWT or access token)

**Query Parameters:**
- `include_revoked` (boolean, optional): Include revoked tokens in response (default: `false`)

**Success Response:**
```json
{
  "status": "success",
  "tokens": [
    {
      "id": "abc123-def456-ghi789",
      "name": "Production Server",
      "token_prefix": "tda_1a2b3c...",
      "created_at": "2025-11-25T10:00:00Z",
      "last_used_at": "2025-11-25T14:30:00Z",
      "expires_at": "2026-02-25T10:00:00Z",
      "revoked": false,
      "revoked_at": null,
      "use_count": 142
    },
    {
      "id": "xyz789-uvw456-rst123",
      "name": "Development",
      "token_prefix": "tda_9z8y7x...",
      "created_at": "2025-11-20T09:00:00Z",
      "last_used_at": null,
      "expires_at": null,
      "revoked": true,
      "revoked_at": "2025-11-24T16:00:00Z",
      "use_count": 23
    }
  ]
}
```

**Token Fields:**
- `token_prefix`: First 10 characters for identification
- `last_used_at`: `null` if never used
- `expires_at`: `null` if no expiration
- `revoked`: Boolean indicating if token has been revoked
- `revoked_at`: Timestamp when token was revoked, `null` if active
- `use_count`: Total number of API calls with this token

**Note:** By default, only active tokens are returned. Set `include_revoked=true` to see revoked tokens in the audit trail.

#### 3.2.3. Revoke Access Token

Immediately revoke an access token, preventing further use. The token is marked as revoked and preserved in the audit trail.

**Endpoint:** `DELETE /api/v1/auth/tokens/{token_id}`  
**Authentication:** Required (JWT or access token)

**URL Parameters:**
- `token_id` (string, required): The token ID to revoke

**Success Response:**
```json
{
  "status": "success",
  "message": "Token revoked successfully"
}
```

**Error Responses:**
- `404 Not Found` - Token not found or doesn't belong to user
- `401 Unauthorized` - Authentication required

⚠️ **Note:** Revoked tokens are kept in the database for audit purposes but cannot be used for authentication. They remain visible in the token list with a "Revoked" status and timestamp. Revoked tokens cannot be reactivated. Create a new token if needed.

### 3.3. Application Configuration

**Note:** Exemplary configuration files for all supported providers can be found in the `docs/RestAPI/scripts/sample_configs` directory.

Initializes and validates the agent's core services, including the LLM provider and the MCP server connection. This is the first step required before creating sessions or submitting queries.

**Endpoint:** `POST /api/v1/configure`  
**Authentication:** Required (JWT or access token) — `401` without one. The configuration is the caller's own; the `reconfiguration` notice goes to the caller's open pages only.

**Request Body:**
    A JSON object containing the full configuration. The structure varies slightly by provider.

**Google, Anthropic, OpenAI:**
```json
{
  "provider": "Google",
  "model": "gemini-1.5-flash-latest",
  "credentials": {
    "apiKey": "YOUR_API_KEY"
  },
  "mcp_server": {
    "name": "my_mcp_server",
    "host": "localhost",
    "port": 8001,
    "path": "/mcp"
  }
}
```

**Friendli:**
```json
{
  "provider": "Friendli",
  "model": "meta-llama/Llama-3.3-70B-Instruct",
  "credentials": {
    "apiKey": "YOUR_FRIENDLI_API_KEY",
    "friendli_endpoint_url": "YOUR_FRIENDLI_ENDPOINT_URL" 
  },
  "mcp_server": { ... }
}
```

**Amazon Bedrock:**
```json
{
  "provider": "Amazon",
  "model": "amazon.titan-text-express-v1",
  "credentials": {
    "aws_access_key_id": "YOUR_AWS_ACCESS_KEY",
    "aws_secret_access_key": "YOUR_AWS_SECRET_KEY",
    "aws_region": "us-east-1"
  },
  "mcp_server": { ... }
}
```

**Ollama:**
```json
{
  "provider": "Ollama",
  "model": "llama2",
  "credentials": {
    "ollama_host": "http://localhost:11434"
  },
  "mcp_server": { ... }
}
```

**Success Response:**
```json
{
  "status": "success",
  "message": "MCP Server 'my_mcp_server' and LLM configured successfully."
}
```

**Error Responses:**
- `400 Bad Request` - Invalid configuration or authentication failed

### 3.4. Session Management

#### 📌 Important: User-Scoped Sessions

All sessions in the REST API are **automatically scoped to the authenticated user**. When you create a session or execute a query, the user identity is extracted from the authentication token (JWT or access token). You do not need to manually specify a user UUID. The system automatically:

- Associates sessions with your authenticated user
- Prevents access to other users' sessions (404 Not Found)
- Isolates all queries and task history by user

This means two different users calling the same endpoints with different authentication tokens will each see their own sessions.

#### 3.4.1. Create a New Session

Creates a new, isolated conversation session for the agent. A session stores context and history for subsequent queries.

**Endpoint:** `POST /api/v1/sessions`  
**Authentication:** Required (JWT or access token)

**Request Body:** Optional. Send `{}` to use your default profile.

| Field | Type | Description |
|---|---|---|
| `profile_id` | string | Bind the session to this profile instead of the default. For a one-query switch, prefer `profile_id` on the query call (§3.5.1) |
| `name` | string | Initial session name |
| `genie_parent_session_id` / `genie_slave_profile_id` | string | Internal — used by a Coordinate (genie) coordinator to create its child sessions |

**Success Response (`201 Created`):**
```json
{
  "session_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
  "session_primer": "optional — present only when the profile configures one"
}
```

**Requirements:**
- User must be authenticated (JWT or access token)
- User must have a configured **default profile** (or pass `profile_id`)
- The effective profile must be **activated** (activation firewall, §3.5.1)
- The profile needs an LLM configuration (all types except Coordinate); a **tool_enabled** (Optimize) profile also needs an MCP server. Coordinate (genie) profiles need neither

**Error Responses:**
- `401 Unauthorized` - Authentication required
- `400 Bad Request` - No default profile configured ("No default profile configured for this user. Please configure a profile (LLM + MCP Server combination) in Anatomy → Agent Profiles first."), or the profile was not found
- `403 Forbidden` - `profile_not_activated` (activation firewall)
- `503 Service Unavailable` - Profile incomplete (missing LLM, or missing MCP server on a tool_enabled profile) or the profile context could not be activated

#### 3.4.2. List Sessions

Get a filtered and sorted list of all user sessions.

**Endpoint:** `GET /api/v1/sessions`  
**Authentication:** Required (JWT or access token)

**Query Parameters:**
- `search` (string, optional): Search query to filter sessions
- `sort` (string, optional): Sort order - `recent`, `oldest`, `tokens`, `turns` (default: `recent`)
- `filter_status` (string, optional): Filter by status - `all`, `success`, `partial`, `failed` (default: `all`)
- `filter_model` (string, optional): Filter by model name (default: `all`)
- `limit` (integer, optional): Maximum results (default: 100)
- `offset` (integer, optional): Pagination offset (default: 0)

**Success Response:**
```json
{
  "sessions": [
    {
      "id": "session-uuid",
      "name": "Data Analysis Session",
      "created_at": "2025-11-19T10:00:00Z",
      "last_updated": "2025-11-19T10:15:00Z",
      "provider": "Google",
      "model": "gemini-1.5-flash",
      "input_tokens": 5000,
      "output_tokens": 3000,
      "turn_count": 3,
      "status": "success"
    }
  ],
  "total": 42
}
```

#### 3.4.3. Get Session Details

Get complete details for a specific session including timeline and RAG associations.

**Endpoint:** `GET /api/v1/sessions/{session_id}/details`  
**Authentication:** Required (JWT or access token)

**URL Parameters:**
- `session_id` (string, required): The session UUID

**Success Response:** Complete session data including `workflow_history`, `execution_trace`, and `rag_cases`

**Error Responses:**
- `404 Not Found` - Session not found
- `401 Unauthorized` - Authentication required

### 3.5. Query Execution

#### 3.5.1. Submit a Query

Submits a natural language query to a specific session. This initiates a background task for the agent to process the query. You can optionally specify a different profile for this query (profile override).

**Endpoint:** `POST /api/v1/sessions/{session_id}/query`  
**Authentication:** Required (JWT or access token)

**URL Parameters:**
- `session_id` (string, required): The session UUID

**Request Body:**
```json
{
  "prompt": "Your natural language query for the agent.",
  "profile_id": "profile-optional-override",
  "extensions": [{"name": "json", "param": null}],
  "skills": [{"name": "concise", "param": null}],
  "attachments": [{"file_id": "file-abc123", "filename": "data.csv"}],
  "canvas_context": {"title": "My Canvas", "language": "python", "content": "...", "modified": false},
  "is_session_primer": false
}
```

**Request Parameters:**
- `prompt` (string, required): The natural language query to submit
- `profile_id` (string, optional): Override the session's default profile with a specific profile ID. If omitted, uses the user's default profile. Use this to execute different queries with different LLM/MCP combinations within the same session. **The effective profile (this override, or the default when omitted) must be ACTIVATED** — see the activation firewall below.
- `extensions` (array, optional): Post-processing extensions to apply to the LLM output. Each element is an object with `name` (string) and optional `param` (string or null). Extensions transform the raw LLM response into structured formats (e.g., JSON extraction, data formatting). See Section 3.20 for managing extensions.
- `skills` (array, optional): Pre-processing skills to inject into the LLM context before execution. Each element is an object with `name` (string) and optional `param` (string or null). Skills are knowledge documents that modify LLM behavior (e.g., enforcing concise answers, SQL expertise). See Section 3.21 for managing skills.
- `attachments` (array, optional): File attachments previously uploaded via `POST /api/v1/chat/upload`. Each element must include `file_id` and `filename`.
- `canvas_context` (object, optional): Bidirectional canvas context with fields `title`, `language`, `content`, and `modified`.
- `is_session_primer` (boolean, optional): When `true`, marks the message as a session initialization message. Defaults to `false`.
- `prompt_name` (string, optional) + `prompt_arguments` (object, optional): Run an MCP prompt in this turn (same as invoking it from the UI resource panel), e.g. `"prompt_name": "base_tableBusinessDesc", "prompt_arguments": {"database_name": "mydb"}`.
- `source` (string, optional): `"flow"` marks the turn's *origin* as a flow (the Flow engine sets it); the transport stays `rest`.

> **Never put an `@TAG` in `prompt`.** `profile_id` alone selects the profile. The REST path does **not** strip a leading `@TAG` (only the web UI does), so a tag left in the prompt is stored verbatim as the user query — it changes the retrieval embedding (a different RAG/knowledge chunk set comes back) and renders as a duplicate of the profile badge. Send the bare question.

**Success Response (`202 Accepted`):**
```json
{
  "task_id": "task-9876-5432-1098-7654",
  "status_url": "/api/v1/tasks/task-9876-5432-1098-7654"
}
```

When the task completes (poll via `GET /api/v1/tasks/{task_id}`), the result includes `extension_results` if extensions were specified:
```json
{
  "status": "complete",
  "result": {
    "final_answer": "<html response>",
    "final_answer_text": "clean text response",
    "extension_results": {
      "json": {"extracted": "data"}
    }
  },
  "extension_results": {"json": {"extracted": "data"}}
}
```

**Error Responses:**
- `404 Not Found` - Session not found (sessions are user-scoped)
- `400 Bad Request` - Missing `prompt`
- `401 Unauthorized` - Authentication required
- `403 Forbidden` - **Profile activation firewall**: the effective profile (override or default) is not usable. Body: `{"error": "...", "code": "<code>", "profile_id": "...", "profile_tag": "..."}` where `code` is `profile_not_activated` (exists but not activated), `profile_not_found` (unknown `profile_id`), `no_profile` (no override and no default), or `activation_check_failed` (the check itself failed — the firewall fails closed). See below.

> **Activation firewall (all query/session/prompt-execution endpoints).** A profile may be driven through the REST API — whether as the user's default or an `@TAG` override — **only if it appears in the user's `active_for_consumption_profile_ids`** — read it with `GET /v1/profiles/active_for_consumption` (§3.10.11a) and set it with `POST /v1/profiles/set_active_for_consumption` (§3.10.12), which is what Anatomy → Agent Profiles writes. Activation is gated on all of the profile's tests passing (LLM connectivity, and for tool-enabled profiles the MCP server), so an untested profile (e.g. one with no valid LLM credentials) is rejected up front with `403 profile_not_activated` instead of failing deep in execution with a misleading error. **`POST /v1/profiles/{id}/activate` does NOT enable API consumption** — despite the name it switches the *profile context* and runs the tests; its response carries `api_consumption_enabled` so the difference is visible (§3.10.8). This applies to `POST /v1/sessions/{id}/query`, `POST /v1/sessions`, `POST /v1/prompts/{name}/execute`, and `POST /v1/prompts/{name}/execute-raw`. **Genie coordinators are exempt** — they drive their validated child profiles internally, so genie slave session creation and slave queries bypass the firewall. The firewall fails **closed** (denies) on any unexpected error.

**Examples:**

Basic query with default profile:
```bash
curl -X POST http://localhost:5050/api/v1/sessions/{session_id}/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is the system version?"}'
```

Query with profile override:
```bash
curl -X POST http://localhost:5050/api/v1/sessions/{session_id}/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Tell me about the current date",
    "profile_id": "profile-1764006444002-z0hdduce9"
  }'
```

Query with skills, extensions, and profile override:
```bash
curl -X POST http://localhost:5050/api/v1/sessions/{session_id}/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "List all tables in the HR database",
    "profile_id": "profile-1764006444002-z0hdduce9",
    "skills": [{"name": "concise", "param": null}],
    "extensions": [{"name": "json", "param": null}, {"name": "extract", "param": null}]
  }'
```

**Profile Visibility in UI:**
- Each message in the session displays the profile badge showing which profile was used
- Messages with the default profile show one badge (e.g., `@GOGET`)
- Messages with an override profile show the override profile badge (e.g., `@FRGOT`)
- The profile badge includes color-coding based on the profile's branding configuration

### 3.6. Task Management

#### 3.6.1. Get Task Status and Result

Polls for the status of a background task. This endpoint provides real-time progress updates through an event log and delivers the final result when the task is complete.

**Endpoint:** `GET /api/v1/tasks/{task_id}`  
**Authentication:** Required (JWT or access token)

**URL Parameters:**
- `task_id` (string, required): The task ID from "Submit a Query"

**Success Response:** See section **4. Data Models - The Task Object** for detailed structure.

**Terminal status — what `complete` promises (Oct 2026).** `complete` means the turn produced a result. A turn that stopped in the engine's unrecoverable-error path and answered nothing ends as `error`, with `result: {"status": "error", "error": "<the engine's message>", "details": "<the cause>"}` — the same `result.error` shape an exception writes. A turn that answered is `complete` even if it recovered from errors on the way (a delegated prompt that failed, a retried call); those stay visible in `events`. Before this, such a turn read `complete` with an empty result.

**Error Responses:**
- `404 Not Found` - Task not found (tasks live in server memory — they do not survive a restart)
- `403 Forbidden` - The task belongs to another user
- `401 Unauthorized` - Authentication required

**Polling pattern:** poll until `status` is one of `complete`, `error`, `cancelled` (the terminal value is `complete`, not `completed`). For push delivery instead of polling, `GET /api/v1/tasks/{task_id}/stream` (SSE) and `GET /api/v1/tasks/{task_id}/events?since=<cursor>` (incremental event list) are also available (§3.13.14).

#### 3.6.2. Cancel Task Execution

Requests cancellation of an actively running background task.

**Endpoint:** `POST /api/v1/tasks/{task_id}/cancel`  
**Authentication:** Required (JWT or access token)

**URL Parameters:**
- `task_id` (string, required): The task ID to cancel

**Request Body:** None

**Success Response:**
```json
{
  "status": "success",
  "message": "Cancellation request sent."
}
```

**Informational Response (Task Already Done):**
```json
{
  "status": "success",
  "message": "Task already completed."
}
```

**Error Responses:**
- `404 Not Found` - Task not found or already completed
- `401 Unauthorized` - Authentication required

### 3.7. RAG Collection Management

#### 3.7.1. Get All RAG Collections

Get all configured RAG collections with their active status.

**Endpoint:** `GET /api/v1/rag/collections`  
**Authentication:** Not required (public endpoint)

**Success Response:**
        ```json
        {
          "status": "success",
          "collections": [
            {
              "id": 1,
              "name": "Support Queries",
              "description": "Customer support query patterns",
              "mcp_server_id": "prod_server",
              "enabled": true,
              "is_active": true,
              "count": 150
            }
          ]
        }
        ```

#### 3.6.2. Create RAG Collection

Create a new RAG collection. All collections must be associated with an MCP server.

* **Endpoint**: `POST /api/v1/rag/collections`
* **Authentication**: Required (JWT or access token)
* **Method**: `POST`
* **Body**:
    ```json
    {
      "name": "Support Queries",
      "description": "Customer support query patterns",
      "mcp_server_id": "prod_server"
    }
    ```
* **Success Response**:
    * **Code**: `201 Created`
    * **Content**:
        ```json
        {
          "status": "success",
          "message": "Collection created successfully",
          "collection_id": 1,
          "mcp_server_id": "prod_server"
        }
        ```
* **Error Response**:
    * **Code**: `400 Bad Request` (if `mcp_server_id` is missing)

#### 3.6.3. Update RAG Collection

Update a RAG collection's metadata (name, description, MCP server association).

* **Endpoint**: `PUT /api/v1/rag/collections/{collection_id}`
* **Authentication**: Required (JWT or access token)
* **Method**: `PUT`
* **URL Parameters**:
    * `collection_id` (integer, required): The collection ID
* **Body**:
    ```json
    {
      "name": "Updated Name",
      "description": "Updated description",
      "mcp_server_id": "new_server"
    }
    ```
* **Success Response**:
    * **Code**: `200 OK`
* **Error Response**:
    * **Code**: `400 Bad Request` (if attempting to remove `mcp_server_id`)
    * **Code**: `404 Not Found` (if collection doesn't exist)

#### 3.6.4. Check Active Sessions for Collection

Check for active (non-archived) sessions that reference a specific collection in their workflow history. Use this endpoint **before deletion** to warn users about sessions that will be archived.

* **Endpoint**: `GET /api/v1/rag/collections/{collection_id}/check-sessions`
* **Authentication**: Required (JWT or access token)
* **Method**: `GET`
* **URL Parameters**:
    * `collection_id` (integer, required): The collection ID
* **Success Response**:
    * **Code**: `200 OK`
    * **Body**:
    ```json
    {
      "status": "success",
      "active_session_count": 3,
      "active_sessions": [
        {
          "session_id": "df8ebb81-5f32-4d4a-be5a-47a036bec54f",
          "session_name": "Sales Analysis Q1"
        },
        {
          "session_id": "a2b1c3d4-e5f6-7890-1234-567890abcdef",
          "session_name": "Product Query"
        }
      ]
    }
    ```
    * **Response Fields**:
        * `active_session_count` (integer) - Total number of active sessions using this collection
        * `active_sessions` (array) - List of up to 5 active sessions (for display purposes)
            * `session_id` (string) - Session UUID
            * `session_name` (string) - User-friendly session name
* **Use Case**:
    * Call this endpoint before showing a delete confirmation dialog
    * Display a dynamic warning like: "⚠️ Warning: 3 active sessions will be archived"
    * Show sample session names to help users understand impact
* **Error Response**:
    * **Code**: `401 Unauthorized` - Authentication required
    * **Code**: `500 Internal Server Error`

#### 3.6.5. Delete RAG Collection

Delete a RAG collection and its vector store. All sessions that reference this collection in their workflow history are automatically archived.

* **Endpoint**: `DELETE /api/v1/rag/collections/{collection_id}`
* **Authentication**: Required (JWT or access token)
* **Method**: `DELETE`
* **URL Parameters**:
    * `collection_id` (integer, required): The collection ID
* **Success Response**:
    * **Code**: `200 OK`
    * **Body**:
    ```json
    {
      "status": "success",
      "message": "Collection deleted successfully",
      "sessions_archived": 3,
      "archived_session_ids": [
        "df8ebb81-5f32-4d4a-be5a-47a036bec54f",
        "a2b1c3d4-e5f6-7890-1234-567890abcdef",
        "b3c2d1e0-f5a6-7891-2345-678901bcdefg"
      ]
    }
    ```
    * **Response Fields**:
        * `sessions_archived` (integer) - Number of sessions automatically archived
        * `archived_session_ids` (array) - List of archived session IDs
    * **Behavior**:
        * All sessions that used this collection for RAG retrieval are archived
        * Archived sessions are marked with `archived: true`, timestamp, and reason
        * Sessions remain accessible but hidden by default (can be shown via "Show Archived" toggle)
        * **Client Behavior** (Web UI):
            * "Show Archived" toggle automatically disables (hides archived sessions)
            * Sessions list automatically refreshes
            * If no active sessions remain, a new session is automatically created
            * User is redirected to the new session (seamless experience)
* **Error Response**:
    * **Code**: `400 Bad Request` - Cannot delete default collection or agent pack-managed collections
    * **Code**: `404 Not Found` - Collection not found

#### 3.6.6. Toggle RAG Collection

Enable or disable a RAG collection.

* **Endpoint**: `POST /api/v1/rag/collections/{collection_id}/toggle`
* **Authentication**: Required (JWT or access token)
* **Method**: `POST`
* **URL Parameters**:
    * `collection_id` (integer, required): The collection ID
* **Body**:
    ```json
    {
      "enabled": true
    }
    ```
* **Success Response**:
    * **Code**: `200 OK`
* **Error Response**:
    * **Code**: `400 Bad Request` (if enabling a collection without MCP server assignment)

#### 3.6.6. Refresh RAG Collection

Refresh the vector store for a specific collection (rebuilds from case files).

* **Endpoint**: `POST /api/v1/rag/collections/{collection_id}/refresh`
* **Authentication**: Required (JWT or access token)
* **Method**: `POST`
* **URL Parameters**:
    * `collection_id` (integer, required): The collection ID
* **Success Response**:
    * **Code**: `202 Accepted`
    * **Content**:
        ```json
        {
          "status": "success",
          "message": "Collection refresh started"
        }
        ```

#### 3.6.7. Submit Case Feedback

Submit user feedback (upvote/downvote) for a RAG case.

* **Endpoint**: `POST /api/v1/rag/cases/{case_id}/feedback`
* **Authentication**: Required (JWT or access token)
* **Method**: `POST`
* **URL Parameters**:
    * `case_id` (string, required): The case UUID
* **Body**:
    ```json
    {
      "feedback_score": 1
    }
    ```
    * `feedback_score`: `-1` (downvote), `0` (neutral), `1` (upvote)
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "message": "Feedback submitted successfully",
          "case_id": "f3a16261-82a9-5d30-a654-64af74f19fcd",
          "feedback_score": 1
        }
        ```
* **Error Response**:
    * **Code**: `400 Bad Request` (invalid feedback_score)
    * **Code**: `404 Not Found` (case not found)

#### 3.7.8. Get Artifact Relationships (Unified Endpoint)

**NEW**: Get comprehensive relationship information for any artifact (collections, profiles, MCP servers, LLM configurations, agent packs). This unified endpoint provides deletion safety analysis and impact assessment.

* **Endpoint**: `GET /api/v1/artifacts/{artifact_type}/{artifact_id}/relationships`
* **Authentication**: Required (JWT or access token)
* **Method**: `GET`
* **URL Parameters**:
    * `artifact_type` (string, required): Type of artifact - one of:
        * `collection` - RAG/knowledge repositories
        * `profile` - Execution profiles
        * `agent-pack` - Agent pack installations
        * `mcp-server` - MCP server configurations
        * `llm-config` - LLM provider configurations
    * `artifact_id` (string, required): The artifact ID
* **Query Parameters**:
    * `include_archived` (boolean, optional, default: `false`): Include archived sessions in results
    * `limit` (integer, optional, default: `5`): Maximum number of sessions to return
    * `full` (boolean, optional, default: `false`): Include extended relationship metadata
* **Example Request**:
    ```bash
    curl -X GET "http://localhost:5050/api/v1/artifacts/collection/12/relationships?include_archived=true&limit=10" \
      -H "Authorization: Bearer $JWT"
    ```
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "artifact": {
            "type": "collection",
            "id": "12",
            "name": "Fitness Handbook (Imported)",
            "repository_type": "knowledge"
          },
          "relationships": {
            "sessions": {
              "active_count": 2,
              "archived_count": 3,
              "total_count": 5,
              "items": [
                {
                  "session_id": "90ad61c9-...",
                  "session_name": "Simple Chat Greeting",
                  "relationship_type": "profile_configuration",
                  "details": "Uses profile @FOCUS which has this collection configured",
                  "is_archived": false
                }
              ],
              "limit_applied": true,
              "has_more": false
            },
            "profiles": {
              "count": 1,
              "items": [
                {
                  "profile_id": "profile-default-rag",
                  "profile_name": "Focus on Fitness",
                  "profile_tag": "FOCUS",
                  "relationship_type": "knowledge_configuration"
                }
              ]
            },
            "agent_packs": {
              "count": 0,
              "items": []
            }
          },
          "deletion_info": {
            "can_delete": true,
            "blockers": [],
            "warnings": [
              "2 active sessions will be archived",
              "1 profile will lose access to this collection"
            ],
            "cascade_effects": {
              "active_sessions_archived": 2,
              "archived_sessions_affected": 3,
              "total_sessions_affected": 5,
              "profiles_affected": 1
            }
          }
        }
        ```
* **Relationship Types**:
    * **For Collections**:
        * `direct_reference` - Session directly uses this collection
        * `workflow_history` - Session queried this collection
        * `profile_configuration` - Session uses profile with collection configured
    * **For Profiles**:
        * `current_profile` - Session currently using this profile
        * `historical_profile` - Session used this profile in past
        * `genie_child` - Genie child session using this profile
    * **For MCP Servers**:
        * `profile_mcp_server` - Session uses profile connected to server
    * **For LLM Configurations**:
        * `profile_llm_config` - Session uses profile with LLM configuration
    * **For Agent Packs**:
        * `uses_pack_profile` - Session uses profile managed by pack
        * `uses_pack_collection` - Session uses collection managed by pack
* **Use Cases**:
    1. **Deletion Warnings**: Check relationships before deleting artifacts
    2. **Impact Analysis**: Understand what will be affected by changes
    3. **Dependency Visualization**: Build artifact dependency graphs
    4. **Audit Trail**: Track which sessions use which resources
* **Error Response**:
    * **Code**: `400 Bad Request` (invalid artifact type)
    * **Code**: `404 Not Found` (artifact not found)
* **Notes**:
    * Provides **single source of truth** for relationship detection
    * Used by both frontend deletion warnings and backend session archiving
    * Replaces older check-sessions endpoints (which remain for backward compatibility)

### 3.8. RAG Template System

The RAG Template System enables automatic generation of RAG case studies through modular templates with LLM-assisted question generation.

#### 3.11.1. List Available Templates

Get all registered Planner Repository Constructors.

* **Endpoint**: `GET /v1/rag/templates`
* **Method**: `GET`
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "templates": [
            {
              "template_id": "sql_query_v1",
              "display_name": "SQL Query Template - Business Context",
              "description": "Two-phase strategy: Execute SQL and generate report",
              "version": "1.0.0",
              "status": "active"
            },
            {
              "template_id": "sql_query_doc_context_v1",
              "display_name": "SQL Query Template - Document Context",
              "description": "Three-phase strategy with document retrieval",
              "version": "1.0.0",
              "status": "active"
            }
          ]
        }
        ```

#### 3.6A.2. Get Template Plugin Info

Get detailed configuration for a specific template including manifest and UI field definitions.

* **Endpoint**: `GET /v1/rag/templates/{template_id}/plugin-info`
* **Method**: `GET`
* **URL Parameters**:
    * `template_id` (string, required): The template identifier (e.g., `sql_query_v1`)
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "template_id": "sql_query_v1",
          "plugin_info": {
            "name": "sql-query-basic",
            "version": "1.0.0",
            "display_name": "SQL Query Template - Business Context",
            "description": "Two-phase strategy...",
            "population_modes": {
              "manual": {
                "enabled": true,
                "input_variables": {
                  "database_name": {
                    "required": true,
                    "type": "string",
                    "description": "Target database name"
                  }
                }
              },
              "auto_generate": {
                "enabled": true,
                "input_variables": {
                  "context_topic": {
                    "required": true,
                    "type": "string",
                    "description": "Business context for generation"
                  },
                  "num_examples": {
                    "required": true,
                    "type": "integer",
                    "default": 5,
                    "min": 1,
                    "max": 1000,
                    "description": "Number of question/SQL pairs to generate"
                  }
                }
              }
            }
          }
        }
        ```

#### 3.6A.3. Generate Questions (LLM-Assisted)

Generate question/SQL pairs using LLM based on schema context and business requirements.

* **Endpoint**: `POST /v1/rag/generate-questions`
* **Method**: `POST`
* **Body**:
    ```json
    {
      "template_id": "sql_query_v1",
      "execution_context": "CREATE TABLE customers (id INT, name VARCHAR(100), email VARCHAR(100), status VARCHAR(20));\nCREATE TABLE orders (id INT, customer_id INT, total DECIMAL(10,2), created_at TIMESTAMP);",
      "subject": "Customer analytics and order reporting",
      "count": 10,
      "database_name": "sales_db"
    }
    ```
    * `template_id`: Template to use for generation
    * `execution_context`: Database schema information (from HELP TABLE, DESCRIBE, etc.)
    * `subject`: Business context topic for question generation
    * `count`: Number of question/SQL pairs to generate (1-1000)
    * `database_name`: Target database name
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "questions": [
            {
              "user_query": "Show all active customers",
              "sql_statement": "SELECT * FROM sales_db.customers WHERE status = 'active';"
            },
            {
              "user_query": "Count total orders by customer",
              "sql_statement": "SELECT customer_id, COUNT(*) as order_count FROM sales_db.orders GROUP BY customer_id;"
            }
          ],
          "input_tokens": 1234,
          "output_tokens": 567
        }
        ```
* **Error Response**:
    * **Code**: `400 Bad Request` (invalid parameters)
    * **Code**: `500 Internal Server Error` (LLM generation failed)

#### 3.6A.4. Generate Questions from Documents

Generate question/SQL pairs from uploaded technical documentation (PDF, TXT, DOC, DOCX). Uses the DocumentUploadHandler abstraction layer with provider-aware processing.

* **Endpoint**: `POST /v1/rag/generate-questions-from-documents`
* **Method**: `POST`
* **Authentication**: Required (JWT token in Authorization header)
* **Content-Type**: `multipart/form-data`
* **Form Data**:
    * `subject` (string, required): Technical domain or documentation topic (e.g., "database performance tuning")
    * `count` (integer, optional): Number of question/SQL pairs to generate (default: 5, max: 1000)
    * `database_name` (string, required): Target database name
    * `target_database` (string, optional): Database type (default: "Teradata")
    * `conversion_rules` (string, optional): SQL dialect conversion rules
    * `files` (file[], required): One or more document files (PDF, TXT, DOC, DOCX)
* **Example Request**:
    ```bash
    curl -X POST http://localhost:5050/api/v1/rag/generate-questions-from-documents \
      -H "Authorization: Bearer YOUR_JWT_TOKEN" \
      -F "subject=performance tuning" \
      -F "count=10" \
      -F "database_name=production_db" \
      -F "target_database=Teradata" \
      -F "files=@dba_guide.pdf" \
      -F "files=@optimization_tips.pdf"
    ```
* **Processing Details**:
    * Provider-aware document handling (Google native upload, Anthropic/Amazon native, others use text extraction)
    * Max file size determined by provider configuration (default: 20MB for Google, 10MB for Anthropic)
    * Documents processed using DocumentUploadHandler abstraction
    * Text extracted and passed to LLM for question generation
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "questions": [
            {
              "question": "How do I identify fragmented tables in Teradata?",
              "sql": "SELECT DatabaseName, TableName, CurrentPerm, PeakPerm FROM DBC.TableSize WHERE (CurrentPerm - PeakPerm) / NULLIFZERO(PeakPerm) > 0.20;"
            },
            {
              "question": "What is the query to find skewed tables?",
              "sql": "SELECT DatabaseName, TableName, Skew FROM DBC.TableSize WHERE Skew > 30;"
            }
          ],
          "count": 2,
          "documents_processed": 2,
          "total_document_size_mb": 15.2,
          "provider": "Google",
          "input_tokens": 2345,
          "output_tokens": 678
        }
        ```
* **Error Response**:
    * **Code**: `401 Unauthorized` (missing or invalid JWT token)
    * **Code**: `400 Bad Request` (validation errors)
        ```json
        {
          "status": "error",
          "message": "Subject is required"
        }
        ```
    * **Code**: `400 Bad Request` (file size exceeded)
        ```json
        {
          "status": "error",
          "message": "File performance_guide.pdf exceeds maximum size of 20MB (actual: 25.3MB)"
        }
        ```
    * **Code**: `503 Service Unavailable` (LLM not configured)
        ```json
        {
          "status": "error",
          "message": "LLM not configured for profile"
        }
        ```

**Notes**:
- Uses user's default profile configuration for LLM provider and model
- Document processing method determined by provider capabilities
- Supports multiple file uploads in single request
- Files are temporarily stored and cleaned up after processing

#### 3.6A.5. Populate Collection from Template

Populate a RAG collection with generated or manual examples using a template.

* **Endpoint**: `POST /v1/rag/collections/{collection_id}/populate`
* **Method**: `POST`
* **URL Parameters**:
    * `collection_id` (integer, required): The collection ID
* **Body**:
    ```json
    {
      "template_type": "sql_query",
      "examples": [
        {
          "user_query": "Show all active customers",
          "sql_statement": "SELECT * FROM sales_db.customers WHERE status = 'active';"
        },
        {
          "user_query": "Count total orders",
          "sql_statement": "SELECT COUNT(*) FROM sales_db.orders;"
        }
      ],
      "database_name": "sales_db",
      "mcp_tool_name": "base_readQuery"
    }
    ```
    * `template_type`: Currently only `"sql_query"` supported
    * `examples`: Array of question/SQL pairs
    * `database_name`: Optional database context
    * `mcp_tool_name`: Optional MCP tool override (default: `base_readQuery`)
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "message": "Successfully populated 2 cases",
          "results": {
            "total_examples": 2,
            "successful": 2,
            "failed": 0,
            "case_ids": [
              "abc123-def456-ghi789",
              "xyz789-uvw456-rst123"
            ],
            "errors": []
          }
        }
        ```
* **Error Response**:
    * **Code**: `400 Bad Request` (validation errors)
        ```json
        {
          "status": "error",
          "message": "Validation failed for some examples",
          "validation_issues": [
            {
              "example_index": 0,
              "field": "sql_statement",
              "issue": "SQL statement is empty or invalid"
            }
          ]
        }
        ```
    * **Code**: `404 Not Found` (collection not found)
    * **Code**: `500 Internal Server Error` (population failed)

### 3.9. MCP Server Management

#### 3.9.1. Get All MCP Servers

Get all configured MCP servers and the active server ID.

* **Endpoint**: `GET /v1/mcp/servers`
* **Method**: `GET`
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "servers": [
            {
              "id": "prod_server",
              "name": "Production Server",
              "host": "localhost",
              "port": 8001,
              "path": "/mcp"
            }
          ],
          "active_server_id": "prod_server"
        }
        ```

#### 3.9.2. Create MCP Server

Create a new MCP server configuration.

* **Endpoint**: `POST /v1/mcp/servers`
* **Method**: `POST`
* **Body**:
    ```json
    {
      "id": "dev_server",
      "name": "Development Server",
      "host": "localhost",
      "port": 8002,
      "path": "/mcp"
    }
    ```
* **Success Response**:
    * **Code**: `201 Created`
* **Error Response**:
    * **Code**: `400 Bad Request` (missing required fields)

#### 3.9.3. Update MCP Server

Update an existing MCP server configuration.

* **Endpoint**: `PUT /v1/mcp/servers/{server_id}`
* **Method**: `PUT`
* **URL Parameters**:
    * `server_id` (string, required): The server ID
* **Body**:
    ```json
    {
      "name": "Updated Name",
      "host": "newhost",
      "port": 8003
    }
    ```
* **Success Response**:
    * **Code**: `200 OK`
* **Error Response**:
    * **Code**: `404 Not Found`

#### 3.9.4. Delete MCP Server

Delete an MCP server configuration. All sessions using profiles connected to this server are automatically archived.

* **Endpoint**: `DELETE /v1/mcp/servers/{server_id}`
* **Method**: `DELETE`
* **URL Parameters**:
    * `server_id` (string, required): The server ID
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "message": "MCP server deleted successfully (5 sessions archived)",
          "archived_sessions": 5
        }
        ```
    * **Response Fields**:
        * `archived_sessions` (integer) - Number of sessions automatically archived
    * **Behavior**:
        * All sessions using profiles connected to this MCP server are archived
        * Archived sessions are marked with `archived: true`, timestamp, and reason
        * Sessions remain accessible but hidden by default (can be shown via "Show Archived" toggle)
        * **Client Behavior** (Web UI):
            * "Show Archived" toggle automatically disables (hides archived sessions)
            * Sessions list automatically refreshes
            * If no active sessions remain, a new session is automatically created
            * User is redirected to the new session (seamless experience)
* **Error Response**:
    * **Code**: `400 Bad Request` (if collections are assigned to this server)

#### 3.9.5. Activate MCP Server

Set an MCP server as the active server for the application.

* **Endpoint**: `POST /v1/mcp/servers/{server_id}/activate`
* **Method**: `POST`
* **URL Parameters**:
    * `server_id` (string, required): The server ID
* **Success Response**:
    * **Code**: `200 OK`
* **Error Response**:
    * **Code**: `404 Not Found`

### 3.10. Profile Management

The Profile Management API provides endpoints for creating, managing, and configuring agent profiles. Profiles define the combination of LLM providers, MCP servers, and execution behaviors that the agent uses to process queries.

**Key Concepts:**
- **Profile Types**: Different execution strategies (tool_enabled, llm_only, rag_focused, genie, conversation_with_tools)
- **Default Profile**: The profile automatically used for new sessions
- **Active for Consumption**: Profiles enabled for multi-profile coordination
- **Classification**: Automatic or manual categorization of MCP tools/prompts/resources
- **Genie Coordination**: Advanced multi-profile orchestration using LangChain agents
- **Session Archiving**: When profiles are deleted, all associated sessions are automatically archived with full traceability

#### Profile Types

| Type | Description | Use Case |
|------|-------------|----------|
| `tool_enabled` | Standard profile with MCP tools + LLM | Data queries, tool execution, standard workflows |
| `llm_only` | Pure conversational LLM without MCP | General chat, reasoning tasks without tool access |
| `rag_focused` | LLM + RAG retrieval only (no MCP tools) | Knowledge base queries, document search |
| `genie` | Multi-agent coordinator profile | Complex queries requiring multiple specialized profiles |
| `conversation_with_tools` | LangChain-based conversation agent | Conversational workflows with tool use |

#### 3.10.1. List All Profiles

Retrieve all profile configurations for the authenticated user.

**Endpoint:** `GET /api/v1/profiles`
**Authentication:** Required (JWT or access token)
**Parameters:** None

**Success Response:**
```json
{
  "status": "success",
  "profiles": [
    {
      "id": "profile-550e8400-e29b",
      "name": "Teradata SQL Agent",
      "tag": "TDAT",
      "description": "Teradata database query and analysis",
      "profile_type": "tool_enabled",
      "color": "#FF6B35",
      "colorSecondary": "#F7931E",
      "llmConfigurationId": "llm-config-123",
      "mcpServerId": "mcp-teradata-456",
      "providerName": "Google",
      "classification_mode": "llm",
      "is_default": true,
      "active_for_consumption": true,
      "authorityScope": {
        "restricted": true,
        "autonomous": true,
        "constraints": ["read-only", "1 tool denied", "$0.5/run cap"]
      },
      "posture": {
        "grounding": {"mode": "enforce", "source": "agent"},
        "corpus":    {"mode": "enforce", "source": "agent"},
        "honesty":   {"mode": "observe", "source": "gate"},
        "outbound":  {"mode": "review",  "source": "gate"},
        "inbound":   {"mode": "enforce", "source": "agent"}
      },
      "created_at": "2026-01-13T10:00:00Z",
      "updated_at": "2026-01-13T12:00:00Z"
    },
    {
      "id": "profile-genie-001",
      "name": "Genie Coordinator",
      "tag": "GENIE",
      "description": "Multi-agent coordination profile",
      "profile_type": "genie",
      "color": "#F59E0B",
      "colorSecondary": "#FCD34D",
      "llmConfigurationId": "llm-config-789",
      "genieConfig": {
        "slaveProfiles": ["profile-550e8400-e29b", "profile-rag-002"]  // Field name preserved for API compatibility
      },
      "is_default": false,
      "active_for_consumption": false,
      "created_at": "2026-01-13T11:00:00Z"
    }
  ]
}
```

**`authorityScope`** (computed, read-only) is the *resolved* agent authority — never derive
this from the raw `authorityConfig`, because a fully-populated block can restrict nothing
(`{autonomous: true, denied_tools: [], deny_writes: false, max_cost_per_run: null}` is
equivalent to no block at all). `restricted` is the verdict, `constraints[]` its wording in
operator-reading order, and `constraints` is empty exactly when `restricted` is `false`.
Note the asymmetries: `allowed_connectors: []` means **deny-all** and `max_cost_per_run: 0`
is a real cap. See [AGENT_AUTHORITY_ARCHITECTURE.md §4a](../Architecture/AGENT_AUTHORITY_ARCHITECTURE.md).

**`actionsParking`** (computed, read-only, Oct 2026; present only while OBDA-MODEL · OBDA-ACT is on and the
agent holds at least one): the granted signed action types whose manifest posture is `review`
— the writes this agent parks for a person instead of performing, read from the agent's own
graph (`action_perform.parking_actions`). The Trust Circle's REVIEWED station counts it beside
the review-mode gates: an agent whose gates all observe but which holds such an action CAN park.

**`authorityConfig.enforce_interactive`** (writable, Aug 2026): by default the whole
authority block applies only to AUTONOMOUS turns (scheduled, flow, genie expert, MCP);
`true` applies it to interactive turns too — it WIDENS the scope, never replaces it, and
only an explicit boolean `true` opts in (a `"true"` string does not — a migrated config
must never silently start denying tools). Turning it off is guarded as a pack-import
downgrade. A partial `PUT` that omits it clears it, like every other authority field.

**`posture`** (computed, read-only) is each answer gate's *resolved effective* mode with
its **source named** — `gate` (the gate's own block), `agent` (inherited from the
agent-level `postureConfig.mode` default), `grounding` (the corpus family following the
grounding gate), or `default` (nothing configured → `observe`). Composite modes are
preserved (`annotate` = observe + disclosure, `review` = enforce + retention). Never
derive a gate's mode from the raw config block: a gate with no own mode inherits the
agent default, and a raw read would disagree with what the runtime actually enforces.
`postureConfig` itself is portable, signed, and downgrade-guarded on agent-pack import
like every other posture block.

**Error Responses:**
- `401 Unauthorized` - Authentication required
- `500 Internal Server Error` - Server error retrieving profiles

---

#### 3.10.2. Get Profile by ID

Retrieve detailed information about a specific profile.

**Endpoint:** `GET /api/v1/profiles/{profile_id}`
**Authentication:** Required (JWT or access token)
**Path Parameters:**
- `profile_id` (string, required) - Profile identifier

**Success Response:**
```json
{
  "status": "success",
  "profile": {
    "id": "profile-550e8400-e29b",
    "name": "Teradata SQL Agent",
    "tag": "TDAT",
    "description": "Teradata database query and analysis",
    "profile_type": "tool_enabled",
    "color": "#FF6B35",
    "colorSecondary": "#F7931E",
    "llmConfigurationId": "llm-config-123",
    "mcpServerId": "mcp-teradata-456",
    "providerName": "Google",
    "classification_mode": "llm",
    "classificationPromptId": "custom-classification-prompt",
    "ragCollectionIds": ["collection-001", "collection-002"],
    "is_default": true,
    "active_for_consumption": true,
    "useMcpTools": true,
    "created_at": "2026-01-13T10:00:00Z",
    "updated_at": "2026-01-13T12:00:00Z"
  }
}
```

---

#### 3.10.3. Create Profile

Create a new profile configuration.

**Endpoint:** `POST /api/v1/profiles`
**Authentication:** Required (JWT or access token)
**Content-Type:** `application/json`

**Request Body:**
```json
{
  "name": "My SQL Agent",
  "tag": "MYSQL",
  "description": "MySQL database queries",
  "profile_type": "tool_enabled",
  "color": "#4285F4",
  "colorSecondary": "#34A853",
  "llmConfigurationId": "llm-config-123",
  "mcpServerId": "mcp-mysql-456",
  "providerName": "Anthropic",
  "classification_mode": "filter",
  "ragCollectionIds": [],
  "useMcpTools": true
}
```

**Required Fields:**
- `name` (string) - Profile display name
- `tag` (string) - Unique tag for @TAG syntax (3-20 uppercase alphanumeric characters)
- `profile_type` (string) - One of: `tool_enabled`, `llm_only`, `rag_focused`, `genie`, `conversation_with_tools`
- `llmConfigurationId` (string) - Reference to LLM configuration

**Optional Fields:**
- `description` (string) - Profile description
- `color` (string) - Primary color (hex code)
- `colorSecondary` (string) - Secondary color (hex code)
- `mcpServerId` (string) - MCP server ID (required for tool_enabled profiles)
- `providerName` (string) - LLM provider name override
- `classification_mode` (string) - `filter` or `llm` (for tool_enabled profiles)
- `classificationPromptId` (string) - Custom classification prompt
- `ragCollectionIds` (array) - RAG collection IDs
- `useMcpTools` (boolean) - Enable MCP tools (for llm_only profiles with conversation)
- `contextLimitOverride` (integer or null) - Profile-level context window cap in tokens. Must be >= 4096 or null. Persistently reduces the model's context limit for all sessions using this profile. Set to `null` to remove override.
- `contextWindowTypeId` (string) - Context window type ID to bind to this profile
- `genieConfig` (object) - Genie-specific configuration (for genie profiles)
- `additionalMcpServers` (array) - PROP-006 phase 2: MCP servers the agent calls BESIDE `mcpServerId`, each `{"id": "<server id>", "alias": "crm", "tools": [...], "prompts": [...]}`. `tools` / `prompts` hold the server's own names and follow the primary's rule (a literal list, `[]` = none, `["*"]` = all). `alias` is lower-case letters and digits with single underscores between them, a letter first, at most 12 characters; the agent sees that server's tools as `<alias>__<name>` while the primary's keep their names. At most 8. Accepted on create (`POST`) and update (`PUT /api/v1/profiles/<id>`); an empty list REMOVES the key (a single-server profile never stores it). Refused with **400** `{"error": "invalid_additional_mcp_servers", "message": "MCP servers: …"}` for a server that is not the user's, the primary itself, listed twice, a bad or repeated alias, or a tool name no provider accepts once qualified; also when `mcpServerId` is set to one of the stored additional servers (remove it there first). Travels with an agent pack by server NAME (the manifest entry's `mcpServers` list — the first server first, then each additional server with its alias and selection); the importer resolves each name, or an explicit alias binding (3.14.3 `mcp_bindings`), to its own server ids and names any server it cannot resolve in `warnings` — never a guess. The alias and selections are part of the agent's signed capability; the server ids are not, so a publisher's signature reinstates on a copy bound to the consumer's own servers. Each server's classification snapshot lives in the deployment-local `additionalMcpClassification`; adding, removing or re-aliasing a server flags reclassification. A turn of such an agent names the server on every MCP tool call it records: the Optimize lane's tool result carries `mcp_source` (`{"server_id", "server", "alias", "reason"}` — `reason` is `qualified` for an `<alias>__` name, `primary` for a plain one, `routed` when Uderia sent the call to the server that holds the data it names; a routed call adds `called` (the tool the model called), `from_server` and `databases`, and the result carries `routing_note`) in the task's events and the turn's `execution_trace`; the ReAct lane's `conversation_tool_invoked` / `conversation_tool_completed` events carry the same object as `source`. Absent for connector, component and `TDA_` tools, and on every turn of a single-server agent.

**Genie Profile Configuration:**
```json
{
  "name": "Genie Coordinator",
  "tag": "GENIE",
  "profile_type": "genie",
  "llmConfigurationId": "llm-config-789",
  "genieConfig": {
    "slaveProfiles": [  // Field name preserved for API compatibility
      "profile-teradata-001",
      "profile-postgres-002",
      "profile-rag-003"
    ]
  }
}
```

**Success Response:**
```json
{
  "status": "success",
  "message": "Profile created successfully",
  "profile": {
    "id": "profile-new-123",
    "name": "My SQL Agent",
    "tag": "MYSQL",
    ...
  }
}
```

**Error Responses:**
- `400 Bad Request` - Invalid request body or duplicate tag
- `401 Unauthorized` - Authentication required
- `500 Internal Server Error` - Server error creating profile

---

#### 3.10.4. Update Profile

Update an existing profile configuration.

**Endpoint:** `PUT /api/v1/profiles/{profile_id}`
**Authentication:** Required (JWT or access token)
**Path Parameters:**
- `profile_id` (string, required) - Profile identifier

**Request Body:** Same structure as Create Profile (all fields optional except those being updated).

**Example — Set Profile Context Limit Override:**
```bash
curl -X PUT "http://localhost:5050/api/v1/profiles/$PROFILE_ID" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"contextLimitOverride": 65536}'
```

This persistently caps the context window to 64K tokens for all sessions using this profile. Set `"contextLimitOverride": null` to remove the override and revert to the model default.

**Success Response:**
```json
{
  "status": "success",
  "message": "Profile updated successfully",
  "profile": {
    "id": "profile-550e8400-e29b",
    "name": "Updated Profile Name",
    ...
  }
}
```

---

#### 3.10.5. Check Active Sessions for Profile

Check for active (non-archived) sessions that use a specific profile. Use this endpoint **before deletion** to warn users about sessions that will be archived.

**Endpoint:** `GET /api/v1/profiles/{profile_id}/check-sessions`
**Authentication:** Required (JWT or access token)
**Method:** `GET`
**Path Parameters:**
- `profile_id` (string, required) - Profile identifier

**Success Response:**
```json
{
  "status": "success",
  "active_session_count": 5,
  "active_sessions": [
    {
      "session_id": "df8ebb81-5f32-4d4a-be5a-47a036bec54f",
      "session_name": "Database Analysis"
    },
    {
      "session_id": "a2b1c3d4-e5f6-7890-1234-567890abcdef",
      "session_name": "Customer Insights"
    }
  ]
}
```

**Response Fields:**
- `active_session_count` (integer) - Total number of active sessions using this profile
- `active_sessions` (array) - List of up to 5 active sessions (for display purposes)
    - `session_id` (string) - Session UUID
    - `session_name` (string) - User-friendly session name

**Use Case:**
- Call this endpoint before showing a delete confirmation dialog
- Display a dynamic warning like: "⚠️ Warning: 5 active sessions will be archived"
- Show sample session names to help users understand impact
- Both regular sessions and Genie child sessions are included in the count

**Error Responses:**
- `401 Unauthorized` - Authentication required
- `500 Internal Server Error`

**Example:**
```bash
# Check active sessions before deleting profile
curl -X GET http://localhost:5050/api/v1/profiles/profile-123/check-sessions \
  -H "Authorization: Bearer $TOKEN"
```

---

#### 3.10.6. Delete Profile

Delete a profile configuration. Cannot delete the default profile.

**Endpoint:** `DELETE /api/v1/profiles/{profile_id}`
**Authentication:** Required (JWT or access token)
**Path Parameters:**
- `profile_id` (string, required) - Profile identifier

**Success Response:**
```json
{
  "status": "success",
  "message": "Profile deleted successfully",
  "sessions_archived": 5,
  "archived_session_ids": [
    "df8ebb81-5f32-4d4a-be5a-47a036bec54f",
    "a2b1c3d4-e5f6-7890-1234-567890abcdef",
    "..."
  ],
  "genie_children_archived": 2
}
```

**Response Fields:**
- `sessions_archived` (integer) - Number of sessions automatically archived
- `archived_session_ids` (array) - List of archived session IDs
- `genie_children_archived` (integer) - Number of Genie child sessions archived

**Behavior:**
- All sessions using this profile are automatically archived (marked as `archived: true` with reason and timestamp)
- Archived sessions remain accessible but are hidden by default in the UI
- Users can view archived sessions by enabling "Show Archived" toggle in Sessions panel
- Genie child sessions linked to this profile are also archived and tracked in `genie_session_links` table
- **Client Behavior** (Web UI):
    * "Show Archived" toggle automatically disables (hides archived sessions)
    * Sessions list automatically refreshes
    * If no active sessions remain, a new session is automatically created
    * User is redirected to the new session (seamless experience)

**Error Responses:**
- `400 Bad Request` - Cannot delete default profile while other profiles exist (safeguard)
- `404 Not Found` - Profile not found
- `401 Unauthorized` - Authentication required

---

#### 3.10.7. Get Default Profile

Retrieve the current default profile information for the authenticated user. This is useful for checking which profile will be used for new sessions or verifying before deletion operations.

**Endpoint:** `GET /api/v1/profiles/default`
**Authentication:** Required (JWT or access token)
**Parameters:** None

**Success Response:**
```json
{
  "status": "success",
  "profile": {
    "id": "profile-550e8400-e29b",
    "name": "Teradata SQL Agent",
    "tag": "TDAT",
    "profile_type": "tool_enabled",
    "llmConfigurationId": "llm-config-123",
    "mcpServerId": "mcp-server-456",
    "description": "Teradata database query and analysis"
  }
}
```

**Use Cases:**
- Check which profile is currently set as default before attempting deletion
- Verify default profile configuration in automation scripts
- Display current default profile in UI/dashboards

**Error Responses:**
- `404 Not Found` - No default profile configured
- `401 Unauthorized` - Authentication required

---

#### 3.10.7. Set Default Profile

Mark a profile as the default profile for the user account. New sessions will automatically use this profile.

**Endpoint:** `POST /api/v1/profiles/{profile_id}/set_default`
**Authentication:** Required (JWT or access token)
**Path Parameters:**
- `profile_id` (string, required) - Profile identifier

**Success Response:**
```json
{
  "status": "success",
  "message": "Default profile set successfully",
  "profile_id": "profile-550e8400-e29b"
}
```

---

#### 3.10.8. Activate Profile

Activate a profile, switching the runtime context to use its configuration. This loads the profile's LLM settings, MCP servers, and classification results. Requires all profile tests to pass.

> **⚠ "Activate" here means the profile CONTEXT — not API consumption.** Two different
> things share the word. This endpoint runs the profile's tests and switches the runtime
> context; whether the profile may be driven through the REST API is a separate list
> (§3.10.11a / §3.10.12) that this endpoint does not write. The response therefore
> reports `api_consumption_enabled`, and adds a `note` when it is `false` — so a
> successful call whose profile still returns `403 profile_not_activated` explains
> itself rather than looking like a bug.

**Endpoint:** `POST /api/v1/profiles/{profile_id}/activate`
**Authentication:** Required (JWT or access token)
**Path Parameters:**
- `profile_id` (string, required) - Profile identifier

**Success Response:**
```json
{
  "status": "success",
  "message": "Profile activated successfully",
  "profile_id": "profile-550e8400-e29b",
  "api_consumption_enabled": false,
  "note": "Profile context switched and all tests passed. This does NOT enable the profile for REST API use — add it to the activation list via POST /v1/profiles/set_active_for_consumption (read the current list from GET /v1/profiles/active_for_consumption first and merge).",
  "test_results": {
    "llm": {"status": "success", "message": "LLM connection successful (Google gemini-3.1-flash-lite)."},
    "mcp_server": {"status": "success", "message": "MCP server configured: Teradata MCP"},
    "rag_collections": {"status": "success", "message": "RAG collections available (2 collection(s))."}
  },
  "details": {
    "llm_loaded": true,
    "mcp_loaded": true,
    "classification_loaded": true
  }
}
```

`note` is present only when `api_consumption_enabled` is `false`.

An agent with additional MCP servers (PROP-006 phase 2) also gets `test_results.additional_mcp_servers` — `{"status": "success" | "warning", "message": "Additional MCP servers — crm: 48 tools; andp: could not be reached (…). …"}`. A server that cannot be reached is a **warning**, never a failure: the agent still answers from its other servers, and every turn names the unavailable one (a Live Status `mcp_server_unavailable` step and a note to the model). Absent for a single-server agent.

**Error Responses:**
- `400 Bad Request` - Profile tests not passed
- `503 Service Unavailable` - Profile activation failed

---

#### 3.10.9. Test Profile

Test a profile's configuration (LLM connectivity, MCP connectivity).

**Endpoint:** `POST /api/v1/profiles/{profile_id}/test`
**Authentication:** Required (JWT or access token)
**Path Parameters:**
- `profile_id` (string, required) - Profile identifier

**Success Response:**
```json
{
  "status": "success",
  "test_results": {
    "llm_test": {
      "passed": true,
      "message": "LLM connection successful",
      "latency_ms": 245
    },
    "mcp_test": {
      "passed": true,
      "message": "MCP server connected successfully",
      "tools_count": 12,
      "prompts_count": 3,
      "resources_count": 5
    }
  }
}
```

---

#### 3.10.9. Get Profile Resources

Get filtered tools, prompts, and resources for a specific profile. Used for real-time resource panel updates when @TAG is typed.

**Endpoint:** `GET /api/v1/profiles/{profile_id}/resources`
**Authentication:** Required (JWT or access token)
**Path Parameters:**
- `profile_id` (string, required) - Profile identifier

**Success Response for tool_enabled profiles:**
```json
{
  "status": "success",
  "tools": {
    "base_readQuery": {
      "name": "base_readQuery",
      "description": "Execute SELECT queries on the database",
      "inputSchema": {
        "type": "object",
        "properties": {
          "sql": {
            "type": "string",
            "description": "The SQL query to execute"
          }
        },
        "required": ["sql"]
      }
    }
  },
  "prompts": {
    "system_prompt": {
      "name": "system_prompt",
      "description": "System initialization prompt"
    }
  },
  "profile_type": "tool_enabled",
  "profile_tag": "TDAT"
}
```

**An agent with several MCP servers** (PROP-006 phase 2) — `tools` and `prompts` hold EVERY server's categories (each additional server's as `<alias>: <category>`, its tools and prompts named `<alias>__<name>` and flagged by that server's own selection), and two keys are added:

- `servers` — one entry per server, the first server first: `{"server_id", "name", "alias" (null for the first server), "state": "classified" | "not_classified", "reason"?, "tools"?, "prompts"?}`. A server not classified on this instance (no snapshot, or one taken in another classification mode — `reason` says which) is listed, never omitted, and contributes no categories.
- `category_servers` — `{category: server_id}` for every MCP category (never the injected `System Tools` / `Component Tools`).

Both keys are absent for a single-server agent, whose response is unchanged.

**Switching one tool or prompt on or off (the resource panel's eye icon):**

`POST /tool/toggle_status` · `POST /prompt/toggle_status` — **Authentication:** required.
Body `{"name": "<tool or prompt>", "disabled": true|false, "profile_id": "<id>"}` (`profile_id`
optional — the caller's default profile). The change is **persisted on the profile** — the same
`tools` / `prompts` selection the profile editor edits, or the matching `additionalMcpServers`
entry for an `<alias>__<name>` — and the runtime's disabled lists are refreshed when that profile
is the loaded one. Switching the last missing name on collapses the list to `["*"]`; switching
one off out of `["*"]` expands it from the server's classification. Refusals (`status: "error"`,
`message` names why): `400` a platform `TDA_*` tool or a missing field · `404` unknown profile or
a name none of the agent's servers offers · `409` the selection is "all" and the server's full list
is not known here (use the profile editor). Prompts are part of the signed capability, so a
toggle on a signed agent reads 🟠 changed until it is re-signed.

A prompt switched off for the agent is never run: `POST /v1/prompts/{name}/execute[-raw]` answers
`403 {"code": "prompt_disabled"}`, and a plan step delegating to it is skipped with a
"Plan Optimization" note.

`POST /api/v1/mcp/resources` (the editor's catalog listing) adds `prompts_error` when listing the
server's prompts **failed** (as opposed to the server not offering prompts — MCP -32601); the
editor then keeps the stored prompt selection instead of saving an empty list.

**Success Response for genie profiles:**
```json
{
  "status": "success",
  "tools": {},
  "prompts": {},
  "profile_type": "genie",
  "profile_tag": "GENIE",
  "slave_profiles": [  // Field name preserved for API compatibility
    {
      "id": "profile-teradata-001",
      "name": "Teradata Agent",
      "tag": "TDAT"
    },
    {
      "id": "profile-rag-002",
      "name": "RAG Knowledge Base",
      "tag": "RAG"
    }
  ]
}
```

**Note:** Genie profiles don't have direct tools/prompts - they coordinate child profiles. The response includes information about coordinated profiles instead.

---

#### 3.10.10. Get Profile Classification

Get the classification results for a specific profile. Returns the cached classification structure (tools, prompts, resources).

**Endpoint:** `GET /api/v1/profiles/{profile_id}/classification`
**Authentication:** Required (JWT or access token)
**Path Parameters:**
- `profile_id` (string, required) - Profile identifier

**Success Response:**
```json
{
  "status": "success",
  "profile_id": "profile-550e8400-e29b",
  "classification_mode": "llm",
  "classification_results": {
    "tools": {
      "query_execution": ["base_readQuery", "base_writeQuery"],
      "schema_inspection": ["base_listDatabases", "base_listTables"],
      "data_operations": ["base_createTable", "base_dropTable"]
    },
    "prompts": {
      "system": ["system_prompt"],
      "examples": ["query_examples"]
    },
    "resources": {
      "documentation": ["teradata_docs", "sql_reference"]
    }
  }
}
```

---

#### 3.10.11. Reclassify Profile

Force reclassification of MCP resources for a specific profile. Clears cached results and re-runs classification using the profile's LLM and mode.

**Endpoint:** `POST /api/v1/profiles/{profile_id}/reclassify`
**Authentication:** Required (JWT or access token)
**Path Parameters:**
- `profile_id` (string, required) - Profile identifier

**Request Body (Optional):**
```json
{
  "classification_mode": "llm"
}
```

**Success Response:**
```json
{
  "status": "success",
  "message": "Profile reclassified successfully",
  "profile_id": "profile-550e8400-e29b",
  "classification_results": {
    "tools": { ... },
    "prompts": { ... },
    "resources": { ... }
  }
}
```

---

#### 3.10.11a. Get Active Profiles for Consumption

Read the list of profiles activated for API consumption — **the list the REST activation
firewall enforces** (§2, "Activation firewall"). A profile absent from it is refused with
`403 profile_not_activated` on every REST profile-resolution endpoint.

**Endpoint:** `GET /api/v1/profiles/active_for_consumption`
**Authentication:** Required (JWT or access token)

**Success Response:**
```json
{
  "profile_ids": ["profile-teradata-001", "profile-rag-003"],
  "profiles": [
    {"id": "profile-teradata-001", "tag": "OPTIM"},
    {"id": "profile-rag-003", "tag": "FOCUS"}
  ],
  "count": 2
}
```

`profiles` carries the tag so a caller can act on names without a second round trip; an
id whose profile no longer exists is returned with `"tag": null` rather than dropped, so
a stale entry is visible instead of silent.

**Always call this before the setter below** — see the warning there.

---

#### 3.10.12. Set Active Profiles for Consumption

Set the list of profiles active for consumption (multi-profile coordination). Updates APP_STATE with enabled/disabled profile lists.

> **⚠ This is a WHOLE-LIST write, not an append.** `profile_ids` *becomes* the list —
> every profile omitted is deactivated for API use. There is no partial-update form.
> The safe sequence is always **read → merge → write**:
> ```bash
> IDS=$(curl -s -H "Authorization: Bearer $JWT" \
>   $BASE/api/v1/profiles/active_for_consumption | jq -c '.profile_ids')
> NEW=$(jq -cn --argjson a "$IDS" --arg id "$ADD" '$a + [$id] | unique')
> curl -s -X POST -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
>   -d "{\"profile_ids\": $NEW}" $BASE/api/v1/profiles/set_active_for_consumption
> ```
> Writing a single id without merging silently deactivates everything else.

**Endpoint:** `POST /api/v1/profiles/set_active_for_consumption`
**Authentication:** Required (JWT or access token)
**Content-Type:** `application/json`

**Request Body:**
```json
{
  "profile_ids": [
    "profile-teradata-001",
    "profile-postgres-002",
    "profile-rag-003"
  ]
}
```

**Success Response:**
```json
{
  "status": "success",
  "message": "Active profiles updated successfully",
  "active_profile_ids": [
    "profile-teradata-001",
    "profile-postgres-002",
    "profile-rag-003"
  ]
}
```

---

### Profile Override in Query Execution

You can override the default profile for individual queries using the `profile_id` parameter in the query submission endpoint:

```bash
# Create session (uses default profile)
SESSION_ID="your-session-id"

# Submit query with profile override
curl -X POST http://localhost:5050/api/v1/sessions/$SESSION_ID/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "What databases are available?",
    "profile_id": "profile-teradata-001"
  }'
```

**Profile Override Behavior:**
- Temporary override for single query only
- Subsequent queries return to default profile
- Useful for multi-profile workflows
- Genie profiles automatically coordinate multiple child profiles

---

### Genie Profile Coordination

Genie profiles provide multi-agent coordination capabilities using LangChain ReAct agents:

**Key Features:**
- **Intelligent Routing**: Automatically routes queries to appropriate child profiles
- **Multi-Profile Consultation**: Can invoke multiple profiles for comprehensive answers
- **Synthesis**: Combines results from multiple profiles into coherent response
- **Session Tracking**: Creates child sessions linked to parent session
- **Visual Hierarchy**: Parent/child session relationships visible in UI

**Genie Profile Structure:**
```json
{
  "profile_type": "genie",
  "genieConfig": {
    "slaveProfiles": [  // Field name preserved for API compatibility
      "profile-teradata-001",    // SQL queries
      "profile-rag-002",          // Knowledge base
      "profile-analytics-003"     // Analytics tools
    ]
  }
}
```

**Genie Execution Flow:**
1. User submits query to Genie profile
2. Genie coordinator analyzes query intent
3. Routes to appropriate child profile(s)
4. Collects responses from invoked profiles
5. Synthesizes final response
6. Returns comprehensive answer to user

**Event Notifications:**
During Genie execution, the following SSE events are emitted:
- `genie_coordination_start` - Coordination begins
- `genie_llm_step` - LLM processing step
- `genie_routing_decision` - Profiles selected
- `genie_slave_invoked` - Child profile called (event name preserved for API compatibility)
- `genie_slave_completed` - Child response received (event name preserved for API compatibility)
- `genie_synthesis_start` - Response synthesis begins
- `genie_coordination_complete` - Coordination complete

**Database Schema:**
Genie coordination creates session links tracked in `genie_session_links` table:
- `parent_session_id` - Parent Genie session
- `slave_session_id` - Child profile session (column name preserved for API compatibility)
- `slave_profile_id` - Profile used for child (column name preserved for API compatibility)
- `slave_profile_tag` - Profile tag (e.g., @TDAT)
- `execution_order` - Order of invocation

---
### 3.11. Session Analytics and Management

#### 3.11.1. Get Session Analytics

Get comprehensive analytics across all sessions for the execution dashboard.

* **Endpoint**: `GET /v1/sessions/analytics`
* **Method**: `GET`
* **Authentication**: Required (JWT or access token)
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "total_sessions": 42,
          "total_tokens": {
            "input": 125000,
            "output": 85000,
            "total": 210000
          },
          "success_rate": 87.5,
          "estimated_cost": 2.10,
          "model_distribution": {
            "gemini-1.5-flash": 60.0,
            "gpt-4": 40.0
          },
          "top_champions": [
            {
              "query": "What databases are available?",
              "tokens": 320,
              "case_id": "abc-123"
            }
          ],
          "velocity_data": [
            {"hour": "2025-11-19 10:00", "count": 5}
          ]
        }
        ```

#### 3.11.2. Get Sessions List

Get a filtered and sorted list of all sessions.

* **Endpoint**: `GET /v1/sessions`
* **Method**: `GET`
* **Authentication**: Required (JWT or access token)
* **Query Parameters**:
    * `search` (string, optional): Search query to filter sessions
    * `sort` (string, optional): Sort order - `recent`, `oldest`, `tokens`, `turns` (default: `recent`)
    * `filter_status` (string, optional): Filter by status - `all`, `success`, `partial`, `failed` (default: `all`)
    * `filter_model` (string, optional): Filter by model name (default: `all`)
    * `limit` (integer, optional): Maximum number of results (default: 100)
    * `offset` (integer, optional): Pagination offset (default: 0)
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "sessions": [
            {
              "id": "session-uuid",
              "name": "Data Analysis Session",
              "created_at": "2025-11-19T10:00:00Z",
              "last_updated": "2025-11-19T10:15:00Z",
              "provider": "Google",
              "model": "gemini-1.5-flash",
              "input_tokens": 5000,
              "output_tokens": 3000,
              "turn_count": 3,
              "status": "success"
            }
          ],
          "total": 42
        }
        ```

#### 3.11.3. Get Session Details

Get complete details for a specific session including timeline and RAG associations.

* **Endpoint**: `GET /v1/sessions/{session_id}/details`
* **Method**: `GET`
* **Authentication**: Required (JWT or access token)
* **URL Parameters**:
    * `session_id` (string, required): The session UUID
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**: Complete session data including `workflow_history`, `execution_trace`, and `rag_cases`
* **Error Response**:
    * **Code**: `404 Not Found`

---


### 3.12. System Prompts Management

The System Prompts API provides endpoints for managing profile-specific prompt mappings. This allows fine-grained control over which prompt versions are used for different functional areas (master system prompts, workflow classification, error recovery, data operations, visualization) on a per-profile basis.

**Key Concepts:**
- **Categories**: Functional areas like `master_system`, `workflow_classification`, `error_recovery`, `data_operations`, `visualization`
- **Subcategories**: Specific providers (for master_system) or functional roles (for other categories)
- **3-Level Fallback**: Profile-specific → System default → Configuration file defaults
- **Active Versions**: Mappings always use the active version of the mapped prompt

#### 3.12.1. Get Available Prompts

Retrieve all available prompts organized by category and subcategory for dropdown population in UI.

* **Endpoint**: `GET /api/v1/system-prompts/available`
* **Method**: `GET`
* **Authentication**: Required (JWT or access token)
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "success": true,
          "categories": {
            "master_system": {
              "Google": [
                {
                  "name": "GOOGLE_MASTER_SYSTEM_PROMPT",
                  "display_name": "Google Master System Prompt",
                  "version": 3
                }
              ],
              "Anthropic": [
                {
                  "name": "MASTER_SYSTEM_PROMPT",
                  "display_name": "Master System Prompt",
                  "version": 2
                }
              ]
            },
            "workflow_classification": {
              "task_classification": [
                {
                  "name": "TASK_CLASSIFICATION_PROMPT",
                  "display_name": "Task Classification",
                  "version": 1
                }
              ]
            },
            "error_recovery": {
              "error_recovery": [...],
              "tactical_self_correction": [...]
            },
            "data_operations": {
              "sql_consolidation": [...]
            },
            "visualization": {
              "charting_instructions": [...],
              "g2plot_guidelines": [...]
            }
          },
          "total_prompts": 15
        }
        ```
* **Error Response**:
    * **Code**: `500 Internal Server Error`

#### 3.12.2. Get Profile Prompt Mappings

Retrieve all prompt mappings for a specific profile.

* **Endpoint**: `GET /api/v1/system-prompts/profiles/{profile_id}/mappings`
* **Method**: `GET`
* **Authentication**: Required (JWT or access token)
* **URL Parameters**:
    * `profile_id` (string, required): The profile UUID
* **Authorization**: Users can only view their own profiles; admins can view all profiles
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "success": true,
          "profile_id": "profile-123-abc",
          "mappings": {
            "master_system": {
              "Google": "GOOGLE_MASTER_SYSTEM_PROMPT",
              "Anthropic": "MASTER_SYSTEM_PROMPT"
            },
            "workflow_classification": {
              "task_classification": "TASK_CLASSIFICATION_PROMPT"
            },
            "error_recovery": {
              "error_recovery": "ERROR_RECOVERY_PROMPT"
            },
            "data_operations": {},
            "visualization": {}
          }
        }
        ```
* **Error Responses**:
    * **Code**: `403 Forbidden` - User does not have permission to view this profile
    * **Code**: `500 Internal Server Error`

**Note:** Empty subcategories (`{}`) indicate the profile uses system defaults for those areas.

#### 3.12.3. Set Profile Prompt Mappings

Set or update prompt mappings for a profile. Supports single or bulk operations.

* **Endpoint**: `POST /api/v1/system-prompts/profiles/{profile_id}/mappings`
* **Method**: `POST`
* **Authentication**: Required (JWT or access token)
* **URL Parameters**:
    * `profile_id` (string, required): The profile UUID
* **Authorization**: Users can only modify their own profiles; admins can modify all profiles
* **Request Body (Single Mapping)**:
    ```json
    {
      "category": "master_system",
      "subcategory": "Google",
      "prompt_name": "GOOGLE_MASTER_SYSTEM_PROMPT"
    }
    ```
* **Request Body (Bulk Mappings)**:
    ```json
    {
      "mappings": [
        {
          "category": "master_system",
          "subcategory": "Google",
          "prompt_name": "GOOGLE_MASTER_SYSTEM_PROMPT"
        },
        {
          "category": "workflow_classification",
          "subcategory": "task_classification",
          "prompt_name": "TASK_CLASSIFICATION_PROMPT"
        }
      ]
    }
    ```
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "success": true,
          "message": "Set 2/2 mapping(s)",
          "results": [
            {
              "status": "success",
              "category": "master_system",
              "subcategory": "Google"
            },
            {
              "status": "success",
              "category": "workflow_classification",
              "subcategory": "task_classification"
            }
          ]
        }
        ```
* **Error Responses**:
    * **Code**: `400 Bad Request` - Missing required fields
    * **Code**: `403 Forbidden` - User does not have permission to modify this profile
    * **Code**: `500 Internal Server Error`

#### 3.12.4. Delete Profile Prompt Mappings

Delete specific prompt mappings or all mappings for a profile (reset to system defaults).

* **Endpoint**: `DELETE /api/v1/system-prompts/profiles/{profile_id}/mappings`
* **Method**: `DELETE`
* **Authentication**: Required (JWT or access token)
* **URL Parameters**:
    * `profile_id` (string, required): The profile UUID
* **Query Parameters** (optional - omit both to delete all mappings):
    * `category` (string, optional): Category to reset
    * `subcategory` (string, optional): Subcategory to reset
* **Authorization**: Users can only modify their own profiles; admins can modify all profiles
* **Success Response (Specific Mapping)**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "success": true,
          "message": "Deleted mapping for master_system/Google"
        }
        ```
* **Success Response (All Mappings)**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "success": true,
          "message": "Deleted all 5 mapping(s) for profile"
        }
        ```
* **Error Responses**:
    * **Code**: `403 Forbidden` - User does not have permission to modify this profile
    * **Code**: `500 Internal Server Error`

**Examples:**

```bash
# Get available prompts
curl -X GET http://localhost:5050/api/v1/system-prompts/available \
  -H "Authorization: Bearer $TOKEN"

# Get profile mappings
curl -X GET http://localhost:5050/api/v1/system-prompts/profiles/profile-123/mappings \
  -H "Authorization: Bearer $TOKEN"

# Set a single mapping
curl -X POST http://localhost:5050/api/v1/system-prompts/profiles/profile-123/mappings \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "category": "master_system",
    "subcategory": "Google",
    "prompt_name": "GOOGLE_MASTER_SYSTEM_PROMPT"
  }'

# Set multiple mappings at once
curl -X POST http://localhost:5050/api/v1/system-prompts/profiles/profile-123/mappings \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings": [
      {"category": "master_system", "subcategory": "Google", "prompt_name": "GOOGLE_MASTER_SYSTEM_PROMPT"},
      {"category": "workflow_classification", "subcategory": "task_classification", "prompt_name": "TASK_CLASSIFICATION_PROMPT"}
    ]
  }'

# Delete specific mapping (reset to system default)
curl -X DELETE "http://localhost:5050/api/v1/system-prompts/profiles/profile-123/mappings?category=master_system&subcategory=Google" \
  -H "Authorization: Bearer $TOKEN"

# Delete all mappings for profile
curl -X DELETE http://localhost:5050/api/v1/system-prompts/profiles/profile-123/mappings \
  -H "Authorization: Bearer $TOKEN"
```

#### 3.12.5. Prompt Editor Endpoints

The System Prompts editor (Anatomy → System Prompts) uses the rest of `/api/v1/system-prompts/*`
(`api/system_prompts_routes.py`, no `/api` re-prefix — the blueprint carries the full path). All
require auth; **writes** additionally require the Prompt Engineer or Enterprise license tier
(`require_prompt_engineer_or_enterprise`, `403` otherwise).

| Method | Path | Purpose | Write |
|---|---|---|---|
| GET | `/api/v1/system-prompts/list` | All prompts with metadata | |
| GET | `/api/v1/system-prompts/<name>` | One prompt's content (decrypted for permitted tiers) | |
| PUT / DELETE | `/api/v1/system-prompts/<name>` | Save a user override / remove the override (back to the base prompt) | yes |
| GET | `/api/v1/system-prompts/<name>/diff` | Base prompt vs current override | |
| GET | `/api/v1/system-prompts/<name>/versions` · `/versions/<n>` | Version history / one version | |
| POST | `/api/v1/system-prompts/<name>/versions/<n>/activate` | Pin a version as active (no new version is created) | yes |
| GET | `/api/v1/system-prompts/<name>/parameters` | Parameters the prompt actually references (global + prompt-specific) | |
| PUT / DELETE | `/api/v1/system-prompts/<name>/parameters/<param>` | Set / clear a parameter override | yes |
| POST | `/api/v1/system-prompts/<name>/duplicate` | Copy under `{new_name, new_display_name?, copy_parameters?}` | yes |
| DELETE | `/api/v1/system-prompts/<name>/delete` | Delete a custom prompt (default prompts cannot be deleted) | yes |
| POST | `/api/v1/system-prompts/bulk/update` | Apply several `{operations: [...]}` at once | yes |
| GET | `/api/v1/system-prompts/profiles` | The caller's profiles for the editor's profile selector | |

---

### 3.13. Document Upload

Upload documents and images to include as context in chat conversations. The platform supports native multimodal processing for providers that support it (Google Gemini, Anthropic Claude, OpenAI GPT-4o, Azure OpenAI, AWS Bedrock Claude) and automatic text extraction fallback for all others.

#### 3.13.1. Get Upload Capabilities

Returns the document upload capabilities for the current user's active profile, including supported formats, size limits, and provider-specific features.

* **Endpoint**: `GET /api/v1/chat/upload-capabilities`
* **Method**: `GET`
* **Authentication**: Required (JWT or access token)
* **Query Parameters**:
    * `session_id` (string, optional): Session UUID to check provider-specific capabilities
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "capabilities": {
            "enabled": true,
            "provider": "Google",
            "model": "gemini-2.5-flash",
            "supported_formats": [".doc", ".docx", ".gif", ".jpeg", ".jpg", ".md", ".pdf", ".png", ".txt", ".webp"],
            "max_file_size_mb": 50,
            "max_files_per_message": 5,
            "text_extraction_formats": [".doc", ".docx", ".md", ".pdf", ".txt"],
            "image_formats": [".gif", ".jpeg", ".jpg", ".png", ".webp"]
          }
        }
        ```

**Example:**
```bash
curl -X GET "http://localhost:5050/api/v1/chat/upload-capabilities?session_id=$SESSION_ID" \
  -H "Authorization: Bearer $TOKEN"
```

#### 3.13.2. Upload Files

Upload one or more files to attach to a chat message. Files are stored server-side and text content is extracted for providers that don't support native document processing. Images are preserved for native multimodal delivery to vision-capable models.

* **Endpoint**: `POST /api/v1/chat/upload`
* **Method**: `POST`
* **Authentication**: Required (JWT or access token)
* **Content-Type**: `multipart/form-data`
* **Form Parameters**:
    * `session_id` (string, required): The session UUID to associate files with
    * `files` (file, required): One or more files to upload (use multiple `files` fields for multiple files)
* **Constraints**:
    * Maximum 5 files per upload
    * Maximum 50 MB per file
    * Supported formats: `.pdf`, `.txt`, `.docx`, `.doc`, `.md`, `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "files": [
            {
              "file_id": "540327ad-1234-5678-abcd-ef0123456789",
              "original_filename": "report.pdf",
              "content_type": "application/pdf",
              "file_size": 45230,
              "extracted_text_preview": "Quarterly Financial Summary...",
              "extracted_text_length": 2450,
              "is_image": false
            },
            {
              "file_id": "6de6b487-abcd-1234-5678-ef0123456789",
              "original_filename": "chart.png",
              "content_type": "image/png",
              "file_size": 4580,
              "extracted_text_preview": "[Image file: chart.png - visual content not available in text mode]",
              "extracted_text_length": 67,
              "is_image": true
            }
          ]
        }
        ```
* **Error Responses**:
    * **Code**: `400 Bad Request` - Missing session_id, no files, unsupported format, file too large, or empty file
    * **Code**: `401 Unauthorized` - Authentication required
    * **Code**: `404 Not Found` - Session not found
    * **Code**: `500 Internal Server Error`

**Example:**
```bash
curl -X POST http://localhost:5050/api/v1/chat/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "session_id=$SESSION_ID" \
  -F "files=@report.pdf" \
  -F "files=@chart.png"
```

#### 3.13.3. Delete Uploaded File

Remove a pending file upload before the message is sent. This deletes the file from server storage and removes it from the session manifest.

* **Endpoint**: `DELETE /api/v1/chat/upload/{file_id}`
* **Method**: `DELETE`
* **Authentication**: Required (JWT or access token)
* **URL Parameters**:
    * `file_id` (string, required): The file UUID returned from the upload endpoint
* **Query Parameters**:
    * `session_id` (string, required): The session UUID the file belongs to
* **Success Response**:
    * **Code**: `200 OK`
    * **Content**:
        ```json
        {
          "status": "success",
          "message": "File deleted"
        }
        ```
* **Error Responses**:
    * **Code**: `400 Bad Request` - Missing session_id
    * **Code**: `401 Unauthorized` - Authentication required
    * **Code**: `404 Not Found` - File not found
    * **Code**: `500 Internal Server Error`

**Example:**
```bash
curl -X DELETE "http://localhost:5050/api/v1/chat/upload/$FILE_ID?session_id=$SESSION_ID" \
  -H "Authorization: Bearer $TOKEN"
```

#### 3.13.4. Submit Query with Attachments

When submitting a query via the SSE streaming endpoint, include the file references from the upload response in the `attachments` field. The platform automatically determines whether to use native multimodal delivery or text extraction based on the active provider's capabilities.

**Request Body (SSE endpoint `/ask_stream`):**
```json
{
  "message": "Analyze this quarterly report and describe the chart",
  "session_id": "session-uuid",
  "attachments": [
    {
      "file_id": "540327ad-1234-5678-abcd-ef0123456789",
      "filename": "report.pdf",
      "content_type": "application/pdf",
      "is_image": false,
      "file_size": 45230
    },
    {
      "file_id": "6de6b487-abcd-1234-5678-ef0123456789",
      "filename": "chart.png",
      "content_type": "image/png",
      "is_image": true,
      "file_size": 4580
    }
  ]
}
```

**Provider-Specific Processing:**

| Provider | Images | PDFs | Other Documents |
|----------|--------|------|-----------------|
| Google Gemini | Native multimodal | Native multimodal | Text extraction |
| Anthropic Claude | Native base64 | Native base64 | Text extraction |
| OpenAI GPT-4o | Native image_url | Text extraction | Text extraction |
| Azure OpenAI (GPT-4o) | Native image_url | Text extraction | Text extraction |
| AWS Bedrock (Claude) | Native multimodal | Native multimodal | Text extraction |
| AWS Bedrock (Nova, etc.) | Text extraction | Text extraction | Text extraction |
| Friendli.AI | Text extraction | Text extraction | Text extraction |
| Ollama | Text extraction | Text extraction | Text extraction |

**Complete Upload + Query Workflow:**
```bash
# 1. Check capabilities
curl -s -X GET "http://localhost:5050/api/v1/chat/upload-capabilities?session_id=$SESSION_ID" \
  -H "Authorization: Bearer $TOKEN" | jq '.capabilities'

# 2. Upload files
UPLOAD_RESULT=$(curl -s -X POST http://localhost:5050/api/v1/chat/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "session_id=$SESSION_ID" \
  -F "files=@report.pdf" \
  -F "files=@chart.png")

echo "$UPLOAD_RESULT" | jq '.files[].file_id'

# 3. Extract file references for the query
FILE1_ID=$(echo "$UPLOAD_RESULT" | jq -r '.files[0].file_id')
FILE2_ID=$(echo "$UPLOAD_RESULT" | jq -r '.files[1].file_id')

# 4. Submit query with attachments via REST API
TASK_RESPONSE=$(curl -s -X POST "http://localhost:5050/api/v1/sessions/$SESSION_ID/query" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"prompt\": \"Analyze this report and describe the chart\",
    \"attachments\": [
      {\"file_id\": \"$FILE1_ID\", \"filename\": \"report.pdf\", \"content_type\": \"application/pdf\", \"is_image\": false, \"file_size\": 45230},
      {\"file_id\": \"$FILE2_ID\", \"filename\": \"chart.png\", \"content_type\": \"image/png\", \"is_image\": true, \"file_size\": 4580}
    ]
  }")

TASK_ID=$(echo "$TASK_RESPONSE" | jq -r '.task_id')

# 5. Poll for results
sleep 10
curl -s -X GET "http://localhost:5050/api/v1/tasks/$TASK_ID" \
  -H "Authorization: Bearer $TOKEN" | jq '.result'
```

#### 3.13.5. Streamed answer text on `/ask_stream` (`answer_delta`) — the Governed Stream

When the deployment's master switch `ANSWER_STREAMING` is on (env `TDA_ANSWER_STREAMING=1`; **off by default**), the UI stream endpoints (`/ask_stream`, `/invoke_prompt_stream`) emit the answer's text **as the model writes it**, ahead of the authoritative `final_answer`. The REST task endpoints (`/v1/sessions/<id>/query`, the scheduler, flows) never carry deltas — a polled task record is not a live consumer.

**SSE event `answer_delta`** (in `seq` order; every engine class — Ideate, Conversation/ReAct, Focus, Optimize, Coordinate):

```json
{"seq": 7, "message_index": 0, "session_id": "…", "turn_id": 3, "text": "There are 100 customers"}
{"seq": 8, "message_index": 1, "session_id": "…", "turn_id": 3, "reset": true}
```

| Field | Meaning |
|---|---|
| `seq` | strictly increasing per turn; a duplicate delivery is dropped by the client |
| `message_index` | the provisional message the text belongs to; a `reset` advances it — the client withdraws the previous text (a retry after an overload error, a model message that turned out to be a tool call, a toxicity halt) |
| `text` | provisional answer text (markdown), coalesced into ≤ one event per 40 ms |
| `html`, `html_upto` | *optional* — the first `html_upto` characters of the current message's text, rendered by **the same formatter function the final answer uses** (`answer_stream.stream_html`: the markdown renderer for Ideate / Focus / Conversation, the report's `_render_direct_answer` while the Optimize report streams its sentence). Complete lines only; an unclosed code fence and a trailing pipe line that may still become a table header are held back. Sent only when the render changed; never on a batch a `reset` withdrew; absent for the Coordinate lane (its final is rendered in the browser with the stream's own renderer). The client draws `html` as-is and only the line being written itself, as the paragraph it will become — so the streamed answer already has the final answer's shape |

**Contract:** `final_answer` stays authoritative — the client replaces every provisional message with it (the trust gates may have redacted, annotated, rewritten or withheld the answer). **It lands in place:** because the stream already carries the final's shape, the UI swaps the provisional message for the authoritative one with no entrance animation and holds the line the reader was on at the same pixel row (the line being written while the view follows, else the line at the reading point, found again in the final by its words; a line the final no longer carries — the Optimize sentence when the report leads with a figure — falls back to the answer's top edge). Nothing scrolls after the landing; what the final adds (marks, notes, table acts, the verdict row) appears where it is. Under an **acting** outbound-guardrail mode (enforce / review) the stream is guarded in flight: PII is redacted before the affected span is released (the same detector as the post-hoc scan, so the two agree), and a toxicity finding withdraws the provisional message. `final_answer` carries an `answer_stream` summary:

```json
"answer_stream": {"deltas": 11, "events": 11, "messages": 1, "provider_streamed": true, "halted": false, "redactions": 0, "first_delta_ms": 6637, "guarded": false}
```

`provider_streamed: false` means the provider has no streaming API for this model family (a Bedrock non-Anthropic model) and the whole text arrived as one delta at the end — said honestly, never pretended.

**`task_start.stream` — the Living Answer (S1.1).** Under the same master the stream's opening `task_start` frame carries `"stream": true` (beside S10's `reattach`), and that word — never a client guess — arms the UI's *Living Answer*: the answer bubble is born at `task_start` with ONE work line in plain words (the phase, elapsed seconds, the turn's tokens) and a phase hairline; every curated step (context assembled, knowledge retrieved with its chunk count, plan, each tool call with its row count and duration, each expert consulted, a considered steer, a recovery, a deterministic rewrite) lands as a row of a timeline receipt in consumer words that opens its Live Status card; at the first `answer_delta` the rows fold into that line (*Worked · 1 query · 4 entities*); the text reveals per batch with a stable block model (a table once its header row closes, a fence once it closes); the gate events (`grounding_gate`, `guardrail`, `honesty`, `compute_lineage`) land as the receipt's last rows; on `final_answer` the chrome moves onto the authoritative message, the marks it carries (`mark.claim-unsupported`, `mark.figure-receipted`, a `[REDACTED:…]` span) land in place, and the ONE verdict chip (Verified · N findings · Not checked — never "clean", never Verified when no check ran) lands on the action row as the answer's single trust control (the capsule's *Why trust this answer?* toggle is folded behind it). A figure quoted from a result row carries that statement's sample rows on the persisted `compute` stamp (`result_sets.mark_quoted`, bounded to `SAMPLE_ROWS`), so the lineage dialog opens on the row after a reload. **The `final_answer` frame carries `checks`** (the checks that ran — `grounding {decision}`, `guardrail {ran, acted, action}`, `honesty {ran, acted, action}`, `compute_lineage {ran, figures, receipts, unreceipted}`; `src/trusted_data_agent/agent/answer_checks.py`, every engine) **and `duration_ms`** (the turn's own clock), because a clean check is quiet on the stream by design: the settle reads them exactly as a reload reads the persisted stamps, so live and reloaded renders derive one verdict. **Reload parity:** `GET /session/<id>` carries `"living_answer": true` under the same master, and the UI rebuilds the identical line / receipt / verdict on every reloaded answer from its `workflow_history` turn through the same reducer. The chip's tooltip says *Sealed in the provenance chain* on `turn_score_complete` (or at the stream's end once the turn is persisted). Master off ⇒ no `stream` word, no `living_answer` flag, no Living Answer, the plain bubble byte-identical. Per viewer: `localStorage.tda_living_answer = "0"` opts out (both paths).

**A stopped turn keeps what arrived.** On a user Stop (`POST /api/session/<id>/cancel_stream`), a timeout or a provider error after text has been delivered, the platform persists the partial answer as the turn (`status: "cancelled" | "error"`, `is_partial: true`, `partial_answer: true`) and emits a `final_answer` with `"partial": true` before the `cancelled`/`error` event, so the bubble is kept and marked *stopped* instead of being replaced by a bare CANCELLED line. The Optimize lane streams the final report's `direct_answer` sentence (never the JSON around it); a turn stopped during planning has no answer text and keeps nothing.

#### 3.13.6. Prompt caching on every LLM call (`cacheConfig`, the turn's `cache` stamp) — the Attested Cache

When the deployment's master switch `PROMPT_CACHING` is on (env `TDA_PROMPT_CACHING=1`; **off by default** — off is byte-identical to before), every LLM call on every engine class places the provider's prompt-cache breakpoints on the **stable prefix** (the system prompt and the conversation so far) and never on the question. What the provider does with them depends on its capability, resolved per call:

| Capability | Providers | What Uderia sends | What it reads |
|---|---|---|---|
| `breakpoints` | Anthropic (natively), OpenRouter fronting an Anthropic model | explicit `cache_control` on the system block and the last history message (≤ 2 breakpoints), `ttl` `5m` (default) or `1h` | `cache_read_input_tokens`, `cache_creation_input_tokens` (broker: `prompt_tokens_details.cached_tokens` / `cache_write_tokens`) |
| `automatic` | OpenAI, Azure, other OpenRouter models | nothing — the provider caches a repeated prefix on its own | `prompt_tokens_details.cached_tokens` |
| `implicit` | Google | nothing — Gemini caches implicitly and reports it | `cached_content_token_count` |
| `none` | Friendli, xAI, Ollama, MLX, Bedrock | nothing | — (the reading abstains) |

**Per-profile key `cacheConfig`** (portable, unsigned — a cost setting never moves a trust badge): `{"enabled": true, "ttl": "5m"|"1h"}`; absent = enabled under the master switch with the 5-minute lifetime. Edited in the profile editor (Capabilities → Prompt Cache).

**The turn's `cache` stamp** (`workflow_history[n].cache`, written at persistence for every engine):

```json
"cache": {"enabled": true, "ttl": "5m", "capability": "breakpoints", "calls": 3,
          "input_tokens": 23957, "cached_tokens": 20596, "written_tokens": 0, "cached_share": 0.8597,
          "hits": 2, "misses_found": 0, "prefix_hash": "497bb6db…", "at": 1789853630.35}
```

`misses_found` is **the attested-prefix rule**: a call whose stable prefix hashed to the SAME bytes as the previous call's, inside the TTL, above the provider's minimum cacheable length for that model (Anthropic: 512 / 1024 / 2048 / 4096 tokens per model — Haiku 4.5 is 4096), on a provider that caches — and nothing came from cache. Every other miss (a first call, a changed prefix, an expired TTL, a short prefix, a provider without a cache) is not a finding. The reading surfaces at the SIGNED station of the assurance dial and as the informational dimension `cache_efficiency`.

**Pricing** is one rule (`CostManager.calculate_cost_with_cache`): cached tokens at the provider's read factor (Anthropic 0.1×, Google 0.25×, OpenAI-shaped 0.5×), an explicit write at the ttl's surcharge (1.25× / 2×). Per-step events (`conversation_llm_step`, `genie_llm_step`, the Ideate completion event) carry `cached_tokens`; the Live Status step card shows a *Cached* row only when something was served from cache (a warm cache is invisible). `turn_cost` is the Σ of the cache-priced calls on every lane.

**Honest limits.** A LangChain-brokered lane in streaming mode cannot see the broker's write count (LangChain drops `token_usage` from streamed responses), so `written_tokens` reads 0 there and the write surcharge is not billed on that lane; the native Anthropic branch reports both. Google's explicit cache and Bedrock breakpoints are not placed (Gemini's implicit cache is read; Bedrock reads nothing).

---
#### 3.13.7. Compute with Lineage (`TDA_Compute`, the turn's `compute` and `compute_lineage` stamps, the `compute_step` provenance step)

When the deployment's master switch `COMPUTE_LINEAGE` is on (env `TDA_COMPUTE_LINEAGE=1`; **off by default** — off is byte-identical to before: the component is absent from every tool catalog, its companion skill never injects, no stamp is written), every data statement a turn executes registers its rows in a per-session **result-set registry** (numbered 1..n in execution order — the same numbering the Live Status theatre shows; framework `TDA_*` tools and row-less results are not statements), and the agent gains a deterministic builtin component **`TDA_Compute`** that computes an exact figure over those rows **in-process, by code** — the model decides *what* to compute, never writes the code, and never pastes rows.

**`TDA_Compute` arguments** (the component's `instructions.json` is the correctness contract; the companion skill `compute-expert` carries the judgement of *when* to compute versus letting the query aggregate):

| Arg | Required | Meaning |
|---|---|---|
| `op` | yes | one of `sum · avg · min · max · count · rank · pivot · filter · pct_change · join · diff` (a closed set — anything else is refused) |
| `column` | no | the column to compute over; optional when the result set has exactly ONE numeric column (several → a refusal naming them) |
| `statement` | no | the statement number to read (1 = the first query this turn; omit for the latest; negative = from the end) |
| `statement_b` | join/diff | the second statement |
| `by`, `agg` | pivot | the group column and the per-group aggregate (`sum` default · `avg` · `min` · `max` · `count`) |
| `n`, `order` | rank | how many rows to keep (default 10), `desc` (default) or `asc` |
| `where` | filter | ONE predicate `column op value`, op ∈ `= != > < >= <= contains startswith` |
| `on`, `order_by` | join | the shared column; an optional ordering of the joined rows |

A refusal (no statement yet, an unknown statement, an ambiguous column, a non-numeric column) is a **recovery-shaped** error carrying `spec.error` — it names the statements available and what to pass — and never renders a card. Empty sets are honest: a `sum` over no rows is `0`, an `avg`/`min`/`max` is `null`, a `rank` is `[]`.

**Every computed figure carries a receipt** — `{op, column, by, on, n, order, where, statements, row_count, row_count_used, columns, value | values | result_rows}` with a content hash `receipt_hash` (sha256 over exactly those fields, canonical JSON). The receipt is (a) **sealed in the turn's Ed25519 provenance chain** as a `compute_step` (`POST /v1/sessions/<id>/provenance/verify` re-derives the hash from the persisted stamp: a receipt that matches no sealed step, or a sealed step whose receipt is gone, is a NAMED mismatch), (b) **persisted on the turn**, (c) **matched against the figures in the delivered answer**.

**The turn's `compute` stamp** (`workflow_history[n].compute`, absent when the turn computed nothing): the receipts, each with up to 50 `sample_rows` (the rows the lineage dialog shows), plus a compact statement index:

```json
"compute": {
  "receipts": [{"op": "sum", "column": "miles", "statements": [1], "row_count": 149, "row_count_used": 149,
                "columns": ["customer", "miles"], "value": 90512.28, "receipt_hash": "3f9a…", "sample_rows": [ … ]}],
  "statements": [{"n": 1, "tool": "base_readQuery", "statement": "SELECT customer, miles FROM …", "columns": ["customer", "miles"], "row_count": 149}]
}
```

**The turn's `compute_lineage` stamp** (`workflow_history[n].compute_lineage`, also the Live Status `compute_lineage` event — *Number Lineage*): every figure in the answer classified ONCE — `receipted` (equals a receipt's value, precision-tolerant incl. 0-dp rounding), `quoted` (equals a cell of a result row, or the number of rows a statement returned), `literals` / `declared` (a value the agent wrote into a statement / a code the knowledge graph declares), `derived` (equals the SUM or AVERAGE of a numeric column of a complete — never truncated — result set; each entry carries `statement`, `column`, `kind` = `sum`|`average`, `value`, `rows`, `lineage`), or `unreceipted` (matches nothing the turn holds). `misderived` lists the unreceipted figures that are within 5% of, but not, a column sum or average (same fields — `value` is what the rows give); they stay in `unreceipted` and the disclosure names the value. Numbers the question itself asked, tokens glued to identifiers (`FB-5000`), and figures of fewer than three digits are never convicted. `decision` is `na` (no result sets or receipts this turn — the reading abstains), `pass` (every figure traceable) or `note` (unreceipted figures worth a disclosure):

```json
"compute_lineage": {"ran": true, "decision": "pass", "receipts": 2, "figures": 3,
  "receipted": [{"figure": "90,512.28", "receipt": 0, "value": 90512.28, "lineage": "SUM(miles) over 149 rows · statement 1"}],
  "quoted":    [{"figure": "149", "statement": 1, "row": 0, "lineage": "the number of rows statement 1 returned"}],
  "unreceipted": [], "reason": "every figure traces to a receipt or a result row"}
```

**In the delivered HTML** the figures are marked in the PROSE only (table cells, `<code>` and `<pre>` are never marked): `<mark class="figure-receipted" data-receipt="0" data-turn-id="…" role="button" tabindex="0" aria-label="…">90,512.28</mark>` and `<mark class="figure-quoted" data-statement="1" data-row="3" …>` (a derived figure wears the same class with `data-row="0" data-derived="sum|average" data-column="…"`) — each carries the platform tooltip with its lineage; click / Enter / Space opens the receipt and its rows in a real dialog (`role="dialog"`, `aria-modal`, focus-trapped, Escape returns focus to the figure; `static/js/handlers/figureLineage.js`, read from `GET /api/session/<sid>/turn/<n>/details` → `compute`). An unreceipted figure gets `figure-unreceipted` (no tooltip claim, no red) and — only when the profile's honesty mode is `annotate` or `enforce` — an append-only disclosure naming the figures. The reading surfaces at the **GROUNDED** station of the assurance dial (mechanism `compute_lineage`), and the `numeric_fidelity` measurement counts a receipted or quoted-by-lineage figure as grounded.

**Optimize planner wiring.** The `compute_insertion` rewrite pass (after `text_date_cast`, before `plan_validation`) inserts ONE `TDA_Compute` phase in front of a synthesis phase whose goal is arithmetic over the data phase before it (`total`, `average`, `top N by`, `change since`, `minimum`, `maximum`, `sum`) — never when the SQL already aggregates (`SUM(`/`TOP n … ORDER BY`), when a phase already computes, when no data phase precedes it, or when the component is disabled for the profile; the inserted phase depends on every earlier data phase, so the scheduler never runs it in parallel with the rows it computes over.

**Honest limits.** A figure the model derives by its own arithmetic is *marked unreceipted*, never rewritten — it may still be right; the note says only that nothing in the turn vouches for it. The exception is a column's sum or average over a complete result set, which the lineage recomputes and traces as `derived`; a sum of a column where summing is meaningless (e.g. a column of averages) is still traced — its lineage names exactly what was summed, and the reader judges it. Rows beyond 5,000 per statement are not held (a compute over more is still exact up to that bound and says `row_count_used`). Ideate and Focus have no data statements and never stamp; Coordinate is covered through its experts.

---
#### 3.13.8. The Resilience Envelope (`llmFallbackConfigurationId`, the turn's `resilience` stamp, `llm_recovery`, `GET /v1/llm/provider-health`)

When the deployment's master switch `RESILIENCE_ENVELOPE` is on (env `TDA_RESILIENCE_ENVELOPE=1`; **off by default** — off is byte-identical: the pre-S4 retry loop, the plain LangChain model classes, no stamp), every LLM call on every lane is wrapped by ONE recovery policy (`llm/resilience.py`), and every act it performs is a **recorded, sealed step**:

| Act | When | What the reader sees |
|---|---|---|
| `retry` | a RETRYABLE provider error — rate limit (429), overloaded (503/529), server error (5xx), timeout, connection — up to `LLM_API_MAX_RETRIES` attempts, base·2^(n−1) backoff capped at 30 s, the provider's `retry-after` honoured up to 60 s (a longer one is "come back later", not a wait); a TERMINAL error (auth, bad request, context too long, not found, anything unknown) fails on the first attempt | a *Provider Retry* Live Status step (reason, wait, attempt) |
| `continuation` | the completion stopped at the output limit (`max_tokens` / `length` / `MAX_TOKENS`) with a non-empty partial: the partial rides as the assistant's own prior turn and the model is asked to continue EXACTLY where it stopped — at most **2** continuations; the parts are joined without a separator | an *Answer Continued* step |
| `fallback` | the primary is exhausted (retryable, out of attempts) or its circuit breaker is OPEN — served by the profile's **signed** fallback (below); a fallback never falls back | a *Served by Fallback* step (from → to, "cost billed at the model that served") and a `served by <model>` chip on the answer |
| `refusal` | the provider declined on content grounds (`refusal`, `content_filter`, `SAFETY`, `RECITATION`, …) — terminal, never retried, never handed to a fallback (a second model must not override a safety decision) | an honest delivered answer naming the refusal category — never a canned "technical error" — and a *Provider Refusal* step |

**The signed fallback — per-profile key `llmFallbackConfigurationId`** (an instance binding like `llmConfigurationId`: never portable, a subscriber has their own configurations; `null`/absent = no fallback — an exhausted provider is then an honest error). Only that one configuration, and only if the caller owns it, is ever used; the primary is never its own fallback; nothing is improvised. Its **resolved identity enters the deployment half of the trust envelope** (`fallback_llm` in the deployment signature — absent when none is bound, so no attestation written before S4 moves), so "which fallback" is itself attested and changing it reads as deployment drift (`describe_drift`: *fallback model changed: none → OpenRouter/anthropic/claude-haiku-4.5*). Edited in the profile editor (Advanced Configuration → Fallback LLM).

**The circuit breaker** (process-wide, per provider): 3 consecutive retryable failures inside 120 s open it for 60 s (doubling per re-trip, capped at 10 min); half-open admits one probe; a success closes it. While open, a turn with a fallback routes to it **immediately** instead of paying the timeout; without a fallback the call is still attempted, once, without backoff.

```
GET /api/v1/llm/provider-health            (JWT / access token)
{"status": "success", "enabled": true,
 "providers": [{"provider": "MLX", "state": "open", "consecutive_failures": 3, "trips": 1, "open_for": 60.0,
                "recovers_in": 41.2, "last_reason": "connection", "last_error_at": 1789864145.5, "degraded": true}, …],
 "degraded": [ …the subset with degraded=true… ]}
```

A breaker transition is also pushed live to the turn's owner (`provider_health` on the notification channel); the chat header shows a strip **only while a provider is degraded** (state, reason, recovery countdown) and nothing at all for a healthy estate.

**The turn's `resilience` stamp** (`workflow_history[n].resilience`; absent when nothing was recovered):

```json
"resilience": {
  "steps": [{"n": 1, "kind": "retry", "reason": "connection", "attempt": 2, "delay": 2.62, "status": null,
             "from": "MLX/dead-model", "to": "MLX/dead-model", "detail": "Connection error.", "at": 1789864108.143},
            {"n": 5, "kind": "fallback", "reason": "connection", "from": "MLX/dead-model", "to": "Google/gemini-3.1-flash-lite", "at": …}],
  "counts": {"retry": 4, "fallback": 1}, "primary": {"provider": "MLX", "model": "dead-model"},
  "served_by": {"provider": "Google", "model": "gemini-3.1-flash-lite"}, "fallback": true, "refusal": null, "at": 1789864100.2}
```

Each step is **sealed into the turn's Ed25519 provenance chain** as an `llm_recovery` step whose content is the timestamp-free `{kind, reason, from, to, attempt, status}` (`POST /v1/sessions/<id>/provenance/verify` re-derives it from the stamp: a sealed step the stamp does not show is a named mismatch). The assurance mechanism `llm_recovery` reads at **SIGNED**: `na` (nothing recovered), `pass` (retries/continuations — the configured model served), `warn` *served by the signed fallback … — a declared, temporary deployment drift for this turn* or *the provider refused (…)*. Cost is billed at the model that **served** (cost invariant #6); `models_used` records the served model.

**Honest limits.** A fallback call inherits the primary's streaming mode (a primary built without streaming — MLX — answers through its fallback whole, not streamed); Anthropic's native server-side refusal fallbacks are not used (refusals are handled uniformly across providers); the Optimize engine pre-records the configured model in `models_used` before its first call, so a session a fallback served lists both the configured and the served model.

---

#### 3.13.9. The Visible Budget (`turnBudget`, the turn's `budget` stamp, the `turn_budget` notice, `budget_adherence`)

When the deployment's master switch `TURN_BUDGET` is on (env `TDA_TURN_BUDGET=1`; **off by default** — off is byte-identical: no note at any tool boundary, no refusal, no stamp, no bar), a profile may declare how much ONE turn may spend and the model **sees what is left**.

**The profile key.** `turnBudget: {steps?, tokens?, seconds?}` — each unit optional, a positive integer (seconds may be fractional); `{}` or absent = no budget. Portable (a pack carries it) and **signed as posture** (how much an agent may spend is posture): each unit is a **ceiling** under the downgrade guard, so a pack that RAISES a ceiling over an installed agent is a posture downgrade unless `allow_posture_downgrade` is passed; absent hashes as `{steps: null, tokens: null, seconds: null}`, so the editor's blank save flips no badge.

```json
PUT /v1/profiles/<id>
{"turnBudget": {"steps": 6, "tokens": 40000, "seconds": 90}}
```

**What the model sees.** `steps` counts DATA tool calls (framework `TDA_*` tools never count); `tokens` sums input + output across the turn's LLM calls; `seconds` is wall-clock since the turn began. At every tool boundary (the two chokepoints every data-running engine passes through) the tool result carries a one-line note — `[Budget] 2 of 6 steps used · ~9k of ~40k tokens used.` — which at 75% of a unit names it as *nearly used*, and once a unit is exhausted becomes the finish instruction (*answer NOW with what you have, and state plainly what is left undone*). **A further data tool call is then REFUSED**, recovery-shaped, naming the tool and the unit — the model answers instead of being killed. The step is reserved AT the gate, so a model that fires N tool calls in one round (parallel tool calls) cannot overrun the budget: the (budget+1)th call in the batch is the one refused. On the Optimize lane the plan is fixed, so the phase loop **skips** the data phases the budget cannot afford (a recorded `turn_budget` step, never silent — a parallel group is trimmed to what is left) and the final report is told to say what remains.

**What the reader sees.** The turn carries a `budget` stamp:

```json
"budget": {
  "units": [{"unit": "steps", "used": 2.0, "total": 2.0, "left": 0.0, "share": 1.0}],
  "exhausted": "steps", "graceful": true, "killed": null, "refusals": 3,
  "started_at": 1789868364.57,
  "finish": "budget exhausted (steps) — stopped gracefully; 2 of 2 steps used"
}
```

`graceful` is true when a unit ran out and the turn finished by the budget's own rule; `killed` names a HARD stop that fired instead (the per-run cost cap `cost_cap`, the ReAct input-token cap `input_token_cap`) — the hard stops stay the last line, one unit above the budget, and a turn they kill is never recorded as graceful. The answer ends with the quiet **finish line** (`finished within budget — …` / `budget exhausted (steps) — stopped gracefully; …`, text and html, `role="note"`). The live bar is fed by a `turn_budget` notice over the per-user notification channel (`payload` = the state + `session_id`) at every tool boundary; the recommender's payload carries the stamp so the dock offers **Continue where it stopped** (Tier A, component-independent) on a budget-stopped turn.

**Assurance.** The `turn_budget` mechanism at BOUNDED (`na` no budget · `pass` within · `warn` exhausted-graceful, acted · `danger` killed) OWNS the informational dimension `budget_adherence` (1.0 within · 0.5 graceful · 0.0 killed · abstains without a stamp; staged at THE RUN). A budget is a boundary and its adherence is a measurement of that boundary — unlike the S2–S4 mechanisms, which own nothing.

#### 3.13.10. The Governed Tool Surface (`toolSurface`, the turn's `tool_surface` stamp, `TDA_FindTools`)

When a profile sets `toolSurface.mode` to `governed` (the editor's *Tools in context: Relevant per turn*; there is **no deployment switch** since Oct 2026 — `all` is byte-identical: every enabled tool is offered on every turn, no stamp), the model is shown **only the tools that could answer this turn** and the rest are held behind a search.

**The profile key.** `toolSurface: {mode: "all" | "governed"}` — absent = `all` (a stored agent keeps its behaviour; an agent created in the editor starts on `governed`; the bootstrap Optimize agents are `governed`). Portable (a pack carries the author's choice) and deliberately **unsigned**: it decides what the model SEES first, never what it MAY do — the profile's enabled tools and Agent Authority stay the boundary, so a governed↔all toggle never moves the badge.

```json
PUT /v1/profiles/<id>
{"toolSurface": {"mode": "governed"}}
```

**The selection (deterministic, 0 tokens).** Over the profile's enabled catalog: the discovery/execution **backbone** (the platform's own `DISCOVERY_TOOLS` + the SQL execution tool) and every framework/component `TDA_*` tool are always offered; a tool whose name / category / description identifier-tokens overlap the question's vocabulary — or the entities and tables the knowledge-graph enrichment named — is offered (a category the question names lifts its whole category); then the tools nearest in **meaning** to the question widen the offered set under one bounded rule (the question embedded against each tool's identity text with the platform's RAG embedder: a floor of 0.18, at least 80% of the question's own best match, at most 5 — `agent/tool_semantics.py`), each carrying its score as its reason; the rest are **held**. Under a governed surface the offered tools carry their full descriptions on every turn. The stamp's `ranker` says what decided (`lexical`, `semantic`, `semantic_model`, `semantic_state` — `ready` · `warming` · `unavailable`, `finder`, `semantic_added`). A governed agent on which `TDA_FindTools` is not offered holds nothing (`ranker.finder: false`). Agent Authority's denials (the explicit deny-list, a write tool under `deny_writes`) are applied first: a denied tool is **never offered and never findable**. On the Optimize lane the surface is decided before the context window is assembled (the `tool_definitions` module renders it) and re-run after the enrichment names the tables in play (it can only widen); on the ReAct lane the model binds offered ∪ found per call while every tool stays executable (a LangGraph dynamic model).

**`TDA_FindTools(query)`** — offered only to governed-surface agents (otherwise the tool does not exist): searches the held set with the same scorer and adds the matching tools to the surface for the rest of the turn (recorded as `widened`); a denied tool the query names is reported as `denied_matching`, never returned; when nothing matches the closest held names are returned so the model can rephrase.

**What the reader sees.** The turn carries a `tool_surface` stamp:

```json
"tool_surface": {
  "mode": "governed",
  "offered": ["base_readQuery", "dba_tableSpace", "..."], "held": ["sec_userGrants", "..."],
  "denied": ["base_writeQuery"], "widened": ["dba_sessionInfo"], "asked_denied": [],
  "finds": 1, "reasons": {"dba_tableSpace": "addresses the question: disk, space", "...": "..."},
  "counts": {"offered": 19, "held": 19, "denied": 1, "widened": 1, "total": 40}
}
```

`asked_denied` names a denied tool the model called or searched for — a named finding. The *Tool Surface* Live Status step (live + reload) lists each set as chips with the reason on hover; the `tool_surface` mechanism at BOUNDED reads `na` (not governed) · `pass` (the split) · `warn` (a denied tool asked for). A `tool_surface` notice rides the per-user notification channel at arming and after every find.

#### 3.13.11. The Outcome Loop (`outcomeConfig`, the turn's `outcome` stamp, the `outcome_revision` step)

When the deployment's master switch `OUTCOME_LOOP` is on (env `TDA_OUTCOME_LOOP=1`; **off by default** — off is byte-identical: the grounding gate's verdict is delivered exactly as before, no stamp) and a profile sets `outcomeConfig.enabled`, a draft answer the grounding gate flags with a **fixable, deterministic finding** gets **one revision** before delivery — and the same gate grades the revision.

**The profile key.** `outcomeConfig: {enabled, maxRevisions, revisableKinds}` — absent = `{enabled: false}` (today's behaviour). `maxRevisions` ∈ {1, 2} (clamped; 0 = off); `revisableKinds` narrows the three kinds the loop may act on — `unsupported_claim` (a span the corpus does not support), `out_of_scope_ref` (a data reference outside the declared scope), `ungrounded_number` (a figure that matches no result); absent or empty = all three. Posture and **signed**: `enabled` is a flag and `maxRevisions` a floor under the downgrade guard (a pack that turns the loop off or lowers the bound is a posture downgrade); an absent block hashes as `{enabled:false}` so the editor's "off" save flips no badge.

```json
PUT /v1/profiles/<id>
{"outcomeConfig": {"enabled": true, "maxRevisions": 1, "revisableKinds": ["ungrounded_number"]}}
```

**The loop (one seam, every lane).** The gate grades the draft; when its decision is `annotate` (never on a `block` — a revision of a withheld answer would be a second silent try) and it names a revisable finding, the model is handed its OWN draft plus a short note — the finding, its exact spans, the fix (*"state only figures that appear in the results"*, *"may reference only: fitness_db"*) — and asked for the revised answer only (one LLM call, history off). The gate re-grades the revision. A revision that **clears** (no revisable finding remains) is delivered clean with its own gate verdict; one that does not delivers the **honest annotated first draft** — never the failed revision. Both drafts are kept and sealed into the turn's Ed25519 provenance chain (`outcome_draft` steps, re-derivable from the stamp by `POST /v1/sessions/<id>/provenance/verify`). A failing reviser fails open (the first draft, verbatim). The revision's tokens and cost are billed to the turn like any other call.

**What the reader sees.** The turn carries an `outcome` stamp (only when a revision ran):

```json
"outcome": {
  "revisions": 1, "cleared": true, "delivered": "revision",
  "findings_before": [{"kind": "ungrounded_number", "spans": ["10"]}], "findings_after": [],
  "drafts": [{"which": "first", "text": "...", "findings": [{"kind": "ungrounded_number", "spans": ["10"]}]},
             {"which": "revision_1", "text": "...", "findings": []}],
  "cost_usd": 0.000054, "tokens_in": 301, "tokens_out": 69,
  "summary": "revised once — ungrounded number removed; the revision passed the gate"
}
```

`delivered` is `revision` or `first`. The *Outcome Revision* Live Status step (live over the notification channel + reload) shows what was found, what remained and the revision's cost; the answer capsule's **Drafts** disclosure shows both drafts side by side with the graded spans marked; the `outcome_loop` mechanism at GROUNDED reads `na` (no revision) · `pass` (cleared) · `warn` (not cleared — the annotated draft was delivered, never a second silent try).

**Honest limit.** The loop inherits the gate's judgement — it is not a second judge. A revision clears when the gate finds nothing revisable in it; a figure the model happens to pick from the results counts as grounded whether or not it answers the question.

#### 3.13.12. Regenerate as an Experiment (`/turns/<n>/regenerate` · `/edit` · `/adopt` · `/branches/<head>/activate` · `/lineage`)

When the deployment's master switch `TURN_LINEAGE` is on (env `TDA_TURN_LINEAGE=1`; **off by default** — off is byte-identical: no lineage keys are ever written, the five endpoints answer `404 turn_lineage_disabled`, and a session serves exactly today's payload), a turn can be **regenerated as a scored sibling** and a question can be **edited and resent as a branch**. Every act is append-only over the session record: a turn is never removed or rewritten; what moves is which sibling stands for a question and which branch the view follows.

**Regenerate.** `POST /v1/sessions/<id>/turns/<n>/regenerate` — body `{profile_id?, attachments?, extensions?, skills?}`. Runs turn `n`'s question again (server-resolved from the record — the body carries no prompt) as a new turn with `sibling_of: <root>`, under the same agent unless `profile_id` names another activated one ("regenerate with another model" is a regenerate under another profile — the platform's unit of model choice); the root's attachments / extensions / skills are carried unless the body supplies its own. **No second user message** is written. The new turn is **adopted on arrival** (the reason to regenerate is to try again); `adopt` switches. Returns the ordinary task `202 {task_id, status_url}`; the task's result and the `rest_task_complete` notice carry `lineage: {kind, of, root, new_turn, branch}`. Every sibling is scored by the evaluation layer like any turn — `GET /v1/evals/turn-score?session_id=…&turn=<sibling>` is the reason to prefer one.

**Edit-and-resend.** `POST /v1/sessions/<id>/turns/<n>/edit` — body `{prompt, profile_id?}`. The new text becomes the **head of a branch** at turn `n` (`branch_of: n`, `branch: <its own number>`); the turns from `n` on — on the branch that was active — are **displaced** (kept in the record, out of the view and out of the model's context) and the branch is active. A regenerate cannot be edited (edit the turn that carries the question); a session primer cannot be regenerated or edited; a turn off the active branch cannot be either.

**Adopt.** `POST /v1/sessions/<id>/turns/<n>/adopt` → `{status, adopted: {root, adopted, siblings}, lineage}`. Makes `n` the sibling the conversation continues from; `400 not_a_sibling_set` on a turn with no siblings.

**Branches.** `POST /v1/sessions/<id>/branches/<head>/activate` (`0` = the root branch) → `{status, branch: {active, restored, displaced, view}, lineage}`. Only the turns whose membership changes move — a purge the reader made on an untouched turn survives. Branches nest (an edit on a branch has that branch as its parent).

**The read.** `GET /v1/sessions/<id>/lineage` → `{lineage}`, `null` when nothing ever regenerated or branched; the UI session GET (`/api/session/<id>`) carries the same object as `lineage` plus `lineage_enabled: true` when the switch is on.

```json
"lineage": {
  "siblings": {"1": {"adopted": 4, "turns": [
      {"turn": 1, "adopted": false, "profile_tag": "IDGGT", "model": "gemini-3.1-flash-lite", "provider": "Google", "in_view": true},
      {"turn": 3, "adopted": false, "profile_tag": "IDGGT", "model": "gemini-3.1-flash-lite", "provider": "Google", "in_view": true},
      {"turn": 4, "adopted": true,  "profile_tag": "IDPURE", "model": "anthropic/claude-haiku-4.5", "provider": "OpenRouter", "in_view": true}]}},
  "branches": {"active": 5, "heads": [{"turn": 5, "of": 2, "parent": 0}], "view": [1, 3, 4, 5]}
}
```

**ONE mechanism for "not in the model's context"** — the existing `isValid` flag every history reader already honours: a non-adopted sibling and a displaced branch turn are invalid for exactly the reason a purged turn is. A sibling's assistant message sits **beside its question** in both histories (numbered with the sibling's own turn), so the model's context stays `user · adopted answer · user · answer …`; a sibling **travels with its question** (it inherits the root's branch) and is visible on every branch that shows the question.

**The UI stream** (`POST /ask_stream`) accepts `regenerate_of: <n>` (with an empty `message`; `profile_override_id` optional — absent, the regenerate runs under the ROOT turn's own agent, exactly as the REST endpoint resolves it, never under the session default) and `edit_of: <n>` (with the new text as `message`); at the stream's end the client re-reads the session — the server is the one source of lineage.

**What the reader sees.** Under the adopted answer, the **Experiment** card: one row per sibling — its agent and model, its verdict strip (the Scorecard's bands, never re-derived), the words that count what needs you, its cost and latency, one **Adopt** per non-adopted row, **Show** for a two-column compare; **Regenerate ▾** on every answer (same agent, or any activated one; `r` on a focused answer), **Edit** on every question (inline; *Resend as a branch*), and on an edited question the branch switch (*Show the original* / *Show the edited version*).

#### 3.13.13. Grounded Multimodal — attachments as sources (`/chat/upload` · `/chat/upload/<file_id>/content` · `attachmentConfig`)

When the deployment's master switch `ATTACHMENT_SOURCES` is on (env `TDA_ATTACHMENT_SOURCES=1`; **off by default** — off is byte-identical: the upload manifest, the document context, the grounding evidence and the persisted turn are exactly today's), every upload is **read by the platform itself, once, at upload**, deterministically where it can be: a **table OCR'd from an image** (rapidocr, else pytesseract — optional extras), a **PDF's tables and text** (pdfplumber, else PyPDF2 text; a scanned PDF is rendered and OCR'd), a **CSV/TSV's rows**, a **text/docx file's text**. What it read is a **source**:

- **verified** — read by the platform. Its text joins the turn's grounding evidence (`RunRecord.grounding_text` and the corpus-support family's retrieved chunks), so the grounding gate holds the answer to it; every verified **table** is registered as an S3 result-set statement (`attachment:<file>#<n> — <method>, verified`), so `TDA_Compute` can operate on its rows and a figure quoted from it traces to the file and its region.
- **unverified** — a transcription the MODEL wrote of an image nothing could read, when the agent's `attachmentConfig.transcribeImages` is on (one vision call per such image, shown in Live Status with its cost). Recorded so the answer can name where it came from; **never evidence** (it does not enter the grounding text) — the assurance reading says the answer leaned on it.
- **unread** — no extractor could read it here; the model saw it natively only. Nothing is vouched for.

**Upload.** `POST /v1/chat/upload` (unchanged shape) additionally returns `source` per file — `{file_id, filename, kind, method, verified, engine, note, width, height, tables:[{index, columns, row_count, region, page, sample_rows}], text_chars}` — and stores the full read on the manifest; `.csv`/`.tsv` join the accepted formats. `GET /v1/chat/upload-capabilities` carries `attachment_sources: {enabled, ocr_engine, pdf_engine}`. For an image or PDF a **verified read replaces the placeholder / plain text** a text-only provider is given (`extracted_text`), so a model that cannot see the image still gets the rows.

**Content.** `GET /v1/chat/upload/<file_id>/content?session_id=<id>` — the stored bytes, session-scoped to their owner (the capsule's crop and the lineage dialog draw the cited region over them). `404` with the switch off.

**The turn.** Every engine (the genie coordinator included) registers its attachments; the persisted turn carries `attachment_sources: {sources:[…summary…], verified, unverified, none, grounding_text}`; `compute.statements[]` carries an attachment statement with `source` + `sample_rows` even when nothing computed over it; the Live Status step **Attachments Read as Sources** (`type: attachment_sources`) and the GROUNDED station's *Attachment sources* reading (`na` none attached · `pass` verified · `warn` an unverified transcription the answer may rest on · `na` "not readable") report it.

**Per agent.** `attachmentConfig: {transcribeImages: bool}` on the profile (portable, unsigned; editor: Trust Configuration → GROUNDED → Attachment Sources). Absent = no transcription.

**What the reader sees.** The composer chip says `read · OCR · 1 table · 3 rows` the moment the upload returns (an image as its own thumbnail, alt text from the extraction; the remove control is a named button); after the turn the chip on the question reads `used as source · …`; *Why trust this answer?* gains an **Attachments** row — each source named with its method and verification, an image as a **crop of the cited region**; a figure quoted from an attachment table opens on the file, the region and the row.

#### 3.13.14. Steer & Reattach — talk to the agent while it works; never lose a running turn (`/sessions/<id>/steer` · `/sessions/<id>/steers` · `/sessions/<id>/active-task` · `/tasks/<task_id>/events` · `/tasks/<task_id>/stream`)

When the deployment's master switch `STEER_REATTACH` is on (env `TDA_STEER_REATTACH=1`; **off by default** — off is byte-identical: `/ask_stream` frames carry no cursor, `task_start` carries no `reattach` flag, no prompt is touched, the persisted turn carries no `steers` stamp, and all five endpoints below answer `404 steer_reattach_disabled`), a running turn has two properties it did not have before:

1. **It survives the connection.** Every frame a UI turn (`POST /ask_stream`) emits is appended to a **per-task event log** and stamped with the SSE-standard `id: <seq>` resume field (a monotonic cursor). A client whose connection dropped — a closed laptop, a tab reload, a proxy timeout — reattaches from the last cursor it saw and receives exactly the frames it missed, then the live tail. The turn itself never noticed: it kept running server-side. The log keeps the last **4000** frames per task and a finished log for **15 minutes**; a replay from below the retained floor is declared as a **gap** (never silently skipped — the client re-reads the session).
2. **It can be steered.** While the turn runs, a short instruction can be queued against its session. It is delivered to the model **at the agent's next step** — prepended to the next tool result (every lane that calls tools), folded into the next tactical prompt (Optimize), or into the final-report prompt (`TDA_FinalReport`) — under an explicit operator lead: *"the user changed the instructions while you were working … where it conflicts with the original request, this wins"*. A steer no step could deliver (a pure LLM turn with no boundary left) is **carried to the next turn** and delivered after that turn's question, under a lead that says so. Nothing is ever silently dropped: every steer is recorded on the persisted turn as **considered at `<step>`** or **carried**.

**Why a note, not a system message.** The Messages API (and every provider the platform speaks) has no mid-conversation `system` role; the tool result is the one place a running conversation accepts new content on every provider, so one mechanism serves all of them. Compliance is the model's — the record says WHERE the steer was considered, the answer shows WHETHER it was followed.

**Endpoints** (all require the session's owner; `404 steer_reattach_disabled` with the switch off).

- `POST /v1/sessions/<session_id>/steer` — body `{"text": "…"}` (≤ 600 chars; at most 5 waiting per session). `202 {status:"queued", steer:{id,text,ts}, task_id, pending}`; `409 no_running_turn` when nothing is running (send it as a question instead); `400 invalid_steer` (empty / too long / queue full).
- `GET /v1/sessions/<session_id>/steers` → `{pending:[{id,text,ts}]}` — the steers not yet considered.
- `GET /v1/sessions/<session_id>/active-task` → `{task_id, last_seq, started, enabled}` (each `null` when nothing is running) — what a fresh tab asks first. With the switch off it answers `200` with `task_id: null, enabled: false` (nothing can be running), not the `404 steer_reattach_disabled` the other endpoints return.
- `GET /v1/tasks/<task_id>/events?since=<cursor>` → `{events:[{seq, frame}], last_seq, done, gap}` — the frames after `since`, for a client that only polls; `frame` is the raw SSE message; `gap` = the first retained cursor when older frames dropped; `400 invalid_cursor`; `404` for a task that is gone or not the caller's.
- `GET /v1/tasks/<task_id>/stream?since=<cursor>` — an SSE tail: the missed frames (each with its `id:`), then live frames as they land, `: ping` keepalives, a leading `event: reattach_gap` when `since` is below the floor, and `event: reattach_end` once the tail closes — its data `{reattached, last_seq, done}` says whether the turn is OVER (`done: true` — an error or a cancel ended it without a final answer; the client does not retry a dead turn) or only the tail closed.

**`/ask_stream` changes** (switch on): every frame carries `id: <seq>`; the `task_start` payload gains `"reattach": true` so a client knows the turn can be reattached.

**The record.** The persisted turn carries `steers: {steers:[{id, text, ts, consumed, at, consumed_ts}], considered, carried}`; a live `steer_consumed` notification (`{steers, at, session_id}`) rides the per-user notification channel the moment a steer is delivered, and the Live Status trace shows **Steer Considered** (with the step it was considered at) or **Steer Carried to the Next Turn** — live and on reload. Honest limit: on a carried steer the next turn's stored `user_query` carries the note (that is what the model was asked).

**What the reader sees.** While an answer streams the composer stays live with the placeholder *Steer the agent — considered at its next step*: Enter sends a steer (a pin under the question — *sent · considered at the next step* → *considered with the result of base_readQuery*), the Stop button still stops. A reload or a fresh tab on a running session shows the turn still running, the trace backfilled from the cursor, and a quiet *reconnected — caught up* note.

### 3.14. Agent Pack Management

Agent packs are pre-configured bundles of profiles and collections that can be installed, shared, and managed as a unit. When deleting agent packs, all associated profiles and collections are removed, and active sessions using those resources are automatically archived.

#### 3.14.1. Check Active Sessions for Agent Pack

Check for active (non-archived) sessions that use any profiles or collections from a specific agent pack. Use this endpoint **before uninstalling** to warn users about sessions that will be archived.

**Endpoint:** `GET /api/v1/agent-packs/{installation_id}/check-sessions`
**Authentication:** Required (JWT or access token)
**Method:** `GET`
**Path Parameters:**
- `installation_id` (integer, required) - Agent pack installation ID

**Success Response:**
```json
{
  "status": "success",
  "active_session_count": 8,
  "active_sessions": [
    {
      "session_id": "df8ebb81-5f32-4d4a-be5a-47a036bec54f",
      "session_name": "Teradata Sales Analysis",
      "reason": "uses profile"
    },
    {
      "session_id": "a2b1c3d4-e5f6-7890-1234-567890abcdef",
      "session_name": "Product Database Query",
      "reason": "uses profile"
    }
  ],
  "profile_ids": [
    "profile-858e8b4c-940b-4689-8182-05a712242286",
    "profile-52f3d13d-edfb-4f74-8d07-e71a0d1356e7"
  ],
  "collection_ids": [
    "13",
    "14",
    "15"
  ]
}
```

**Response Fields:**
- `active_session_count` (integer) - Total number of active sessions affected
- `active_sessions` (array) - List of up to 5 active sessions (for display purposes)
    - `session_id` (string) - Session UUID
    - `session_name` (string) - User-friendly session name
    - `reason` (string) - Why this session is affected ("uses profile" or "uses collection")
- `profile_ids` (array) - List of profile IDs in this pack
- `collection_ids` (array) - List of collection IDs in this pack

**Use Case:**
- Call this endpoint before showing an uninstall confirmation dialog
- Display a dynamic warning like: "⚠️ Warning: 8 active sessions will be archived"
- Show sample session names to help users understand the impact
- List which profiles and collections will be removed

**Error Responses:**
- `401 Unauthorized` - Authentication required
- `404 Not Found` - Agent pack not found
- `500 Internal Server Error`

**Example:**
```bash
# Check active sessions before uninstalling agent pack
curl -X GET http://localhost:5050/api/v1/agent-packs/2/check-sessions \
  -H "Authorization: Bearer $TOKEN"

# Example: Dynamic confirmation message in UI
# If active_session_count > 0:
#   "⚠️ Warning: 8 active sessions will be archived."
#   "Affected sessions:"
#   "• Teradata Sales Analysis"
#   "• Product Database Query"
#   "...and 6 more"
```

#### 3.14.2. Uninstall Agent Pack

Uninstall an agent pack, removing all associated profiles, collections, and knowledge graphs. All sessions using these resources are automatically archived.

**Endpoint:** `DELETE /api/v1/agent-packs/{installation_id}`
**Authentication:** Required (JWT or access token)
**Method:** `DELETE`
**Path Parameters:**
- `installation_id` (integer, required) - Agent pack installation ID

**Success Response:**
```json
{
  "status": "success",
  "profiles_deleted": 10,
  "collections_deleted": 9,
  "kgs_deleted": 2,
  "profiles_kept": 0,
  "collections_kept": 0,
  "sessions_archived": 8
}
```

**Response Fields:**
- `profiles_deleted` (integer) - Number of profiles removed
- `collections_deleted` (integer) - Number of collections removed
- `kgs_deleted` (integer) - Number of knowledge graphs removed (v1.4+ packs)
- `profiles_kept` (integer) - Profiles retained (shared with other packs)
- `collections_kept` (integer) - Collections retained (shared with other packs)
- `sessions_archived` (integer) - Number of sessions automatically archived

**Behavior:**
- All profiles, collections, and knowledge graphs owned by this pack are deleted
- KG deletion cascades across `kg_metadata`, `kg_entities`, `kg_relationships`, and `kg_profile_assignments`
- Resources shared with other installed packs are kept
- All sessions using deleted resources are automatically archived
- Archived sessions include reason and timestamp
- Cannot uninstall if a deleted profile is set as default (change default first)
- **Client Behavior** (Web UI):
    * "Show Archived" toggle automatically disables (hides archived sessions)
    * Sessions list automatically refreshes
    * If no active sessions remain, a new session is automatically created
    * User is redirected to the new session (seamless experience)

**Error Responses:**
- `404 Not Found` - Agent pack not found
- `403 Forbidden` - Not the pack owner
- `400 Bad Request` - Cannot delete default profile
- `500 Internal Server Error`

**Example:**
```bash
# Uninstall agent pack
curl -X DELETE http://localhost:5050/api/v1/agent-packs/2 \
  -H "Authorization: Bearer $TOKEN"
```

---

#### 3.14.3. Import Agent Pack

Import an `.agentpack` file to install profiles and collections as a bundle.

**Endpoint:** `POST /api/v1/agent-packs/import`
**Authentication:** Required (JWT or access token)

**Request:** Multipart form data OR JSON

**Option 1: File Upload (multipart/form-data)**

**Form Fields:**
- `file` (file, required): `.agentpack` file to import
- `mcp_server_id` (string, optional): MCP server ID to associate profiles with (the pack-level fallback)
- `mcp_bindings` (JSON object, optional): **explicit per-agent MCP binding** `{"<profile_tag>": "<mcp_server_id>"}` — the consumer's own choice per agent (Sep 2026). Keys are the tags the pack SHIPS (under `conflict_strategy=expand` they follow the renamed tags automatically); values must be MCP servers **the importing user owns** — a foreign id is refused with `400` naming the tag, never silently unbound. Precedence per agent: explicit binding → manifest `mcpServerName` auto-match against the user's servers (case-insensitive) → pack-level `mcp_server_id`. For an agent that reads several MCP servers the value may be an object `{"primary": "<id>", "<alias>": "<id>", …}` — `primary` binds the agent's first server, each alias one of its additional servers (a foreign id is refused naming `TAG:alias`). A server left out falls back to its manifest name. In multipart form data the field is the JSON string. Use `POST /api/v1/agent-packs/inspect` first to learn which agents need a server and what each currently resolves to.
- `llm_configuration_id` (integer, optional): LLM configuration ID for profiles
- `conflict_strategy` (string, optional): How to handle conflicts. Accepted values: `replace`, `expand`, `update_in_place`. See **Conflict Strategies** below for semantics.

> **Knowledge graphs under `update_in_place` (Sep 2026):** each pack-owned graph shipped for a profile the manifest still carries is refreshed **in place** — snapshotted, cleared, re-imported with its `kg_id`, assignments and attestation identity kept; graphs the pack does not own are never touched. The response carries `kgs_refreshed`, `kgs_created` and `kgs_moved`. A consumer's graph has no source of its own (resync reports `not_resyncable`); the pack is its source. **The graph topology may change between releases:** when several manifest graphs resolve to the same consumer graph (the pack SPLIT one graph into several), the graph stays with the entry the manifest names as its owner and every other entry gets a graph of its own — created under its manifest owner, recorded as a pack resource, the profile's active assignment moved onto it (`kgs_created`, `kgs_moved`); when one manifest graph carries profiles that were on several pack graphs (a MERGE), they are moved onto the kept graph and the graph they left is kept, inactive, and named in `warnings`. A consumer's own graph (not the pack's) is never displaced; a refreshed graph's name and description follow the manifest.
- `force_drop_tags` (boolean, optional, `update_in_place` only): Allow the new manifest to omit tags that the previously installed pack had. When `true`, those profiles are removed and their sessions archived. Defaults to `false` — drops are refused with `error="dropped_tags"` so a publisher can't accidentally remove a subscriber-facing profile.
- `allow_posture_downgrade` (boolean, optional, `update_in_place` only): Allow the new manifest to REDUCE an existing profile's trust posture — a weaker grounding/guardrail/honesty mode, a disabled detector, or a widened agent authority (re-enabled autonomy, shrunk `denied_tools`, widened `allowed_connectors`, raised or removed `max_cost_per_run`). Defaults to `false`: because the profile merge replaces a config block wholesale, a republish would otherwise silently switch off a hardened agent's enforcement. Refused with `error="posture_downgrade"`, and the message names each weakened key. A block the manifest omits is never a downgrade — absence preserves the subscriber's own value.

**Machine-readable error codes.** Validation failures return HTTP 400 with `status="error"`, a human `message`, and an `error` field drawn from a closed set: `dropped_tags`, `embedding_model_lock_violation`, `ambiguous_target_installation`, `posture_downgrade`. (Before Jul 2026 the code appeared only as a prefix inside `message`; clients that string-matched still work.)
- `dry_run` (boolean, optional, `update_in_place` only): Validate and compute the diff plan without committing. Returns `status="dry_run"` plus `would_preserve` / `would_change` blocks. Combining `dry_run=true` with any other strategy returns `400 dry_run_not_supported_for_strategy`.

**Option 2: Local File Path (application/json)**

**Request Body:**
```json
{
  "import_path": "/path/to/pack.agentpack",
  "mcp_server_id": "mcp_server_123",
  "mcp_bindings": {"NIBRS": "server-nibrs-uuid", "CASEDF": "server-case-uuid"},
  "llm_configuration_id": 1,
  "conflict_strategy": "update_in_place",
  "force_drop_tags": false,
  "allow_posture_downgrade": false,
  "dry_run": false
}
```

**Success Response:**
```json
{
  "status": "success",
  "installation_id": 5,
  "pack_name": "Sales Analytics Pack",
  "profiles_imported": 4,
  "collections_imported": 3,
  "kgs_created": 2,
  "conflicts_resolved": 1,
  "summary": {
    "profiles": ["Sales Dashboard", "Revenue Analysis", "Customer Insights", "Trend Forecasting"],
    "collections": ["Sales Queries", "Revenue Metrics", "Customer Data"]
  }
}
```

**Response Fields:**
- `kgs_created` (integer) - Number of knowledge graphs created (v1.4+ packs only)

**Conflict Strategies:**
- *(omitted / null)* — Refuse to install if any tag conflicts with an existing profile (default, backward-compatible).
- `replace` — Delete existing conflicting profiles + their orphaned collections + the publisher's `agent_pack_installations` row + the marketplace listing row, then install fresh. Generates **new** IDs for everything. **Breaks marketplace subscribers** — their `CollectionSubscription.source_collection_id` rows dangle. Use only on packs that are not published or have no subscribers.
- `expand` — Auto-rename profile tags to avoid collision (e.g., `TAG` → `TAG2`). Old install untouched, new install runs alongside.
- `update_in_place` — Re-import on top of an existing pack while preserving every subscriber-visible identifier: `agent_pack_installations.id`, `marketplace_agent_packs.id`, `profile.id` (per matching tag), `collection.id` (per matching ref). Documents are diffed at the chunk level — unchanged docs are skipped, changed docs re-embedded, new docs added, dropped docs removed (only for known refs). Per-document CDC config (`source_uri`, `sync_enabled`) is preserved on docs that already had it. CDC poller is paused for affected collections during the import (`next_sync_at` set to NULL, restored after). Refuses to drop manifest-missing profile tags unless `force_drop_tags=true`. Refuses to weaken an existing profile's trust posture unless `allow_posture_downgrade=true` (`error="posture_downgrade"`). Refuses if old pack profiles span multiple installation IDs (`error="ambiguous_target_installation"`). Refuses if a target collection has `embedding_model_locked=1` and the manifest declares a different embedding model (`error="embedding_model_lock_violation"`). Required for any pack that is published to the marketplace.

**Update-in-place response schema (commit):**
```json
{
  "status": "updated_in_place",
  "installation_id": 42,
  "marketplace_listing_id": "abc-123-uuid",
  "name": "Uderia Expert",
  "coordinator_tag": null,
  "coordinator_profile_id": null,
  "profiles_updated": 1,
  "profiles_added": 0,
  "profiles_dropped": 0,
  "collections_changed": 1,
  "docs_added": 3,
  "docs_updated": 7,
  "docs_removed": 1,
  "docs_unchanged": 142,
  "archived_sessions": 0,
  "resource_reconciliation": {"added": 0, "updated": 1, "removed": 0}
}
```

**Update-in-place response schema (dry-run):**
```json
{
  "status": "dry_run",
  "would_preserve": {
    "installation_id": 42,
    "marketplace_listing_id": "abc-123-uuid",
    "profile_ids_by_tag": {"UDERIA": "profile-…"},
    "collection_ids_by_ref": {"uderia_knowledge": 17}
  },
  "would_change": {
    "profiles": {"updated": ["UDERIA"], "added": [], "dropped": []},
    "collections": [
      {"ref": "uderia_knowledge", "id": 17, "action": "diff_patch",
       "docs_unchanged": 142, "docs_updated": 7, "docs_added": 3, "docs_removed": 1}
    ],
    "marketplace_listing": {"action": "update", "listing_id": "abc-123-uuid",
                            "version_change": "1.0.0 -> 1.0.1"}
  },
  "validation_errors": []
}
```

If validation fails in dry-run, `status="dry_run_failed"` and `validation_errors` lists the problems. The committing path raises a 400 with the same error message in `message`.

**Error Responses:**
```json
// Invalid .agentpack file
{
  "status": "error",
  "message": "Invalid agent pack format: Missing manifest.json"
}

// File not found
{
  "status": "error",
  "message": "File not found: /path/to/pack.agentpack"
}

// Profile tag conflict (HTTP 409)
{
  "status": "tag_conflict",
  "conflicting_tags": ["VAT", "PRODUCT_SME"],
  "message": "Tag conflict: profiles @VAT, @PRODUCT_SME already exist"
}
```

**Example:**
```bash
# Import via file upload
curl -X POST http://localhost:5050/api/v1/agent-packs/import \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@sales-analytics.agentpack" \
  -F "mcp_server_id=mcp_server_123" \
  -F "conflict_strategy=rename"
```

**Notes:**
- v1.3+ packs: bundled user skills are automatically installed to the user skills directory before profiles are created; skill manager is reloaded so auto-enabled skill assignments in `skillsConfig` are immediately active
- `conflict_strategy` valid values: `replace`, `expand`, `update_in_place`. See **Conflict Strategies** above for full semantics.
- **For published / subscribed packs always use `update_in_place`** — `replace` deletes the marketplace listing and orphans subscribers' `CollectionSubscription` rows.
- `dry_run=true` is only honoured with `update_in_place`. Combining it with `replace` or `expand` returns `400 dry_run_not_supported_for_strategy`.

**Client Behavior:**
- Show upload progress bar
- Display conflict resolution options if conflicts detected
- Refresh profiles and collections lists after import

---

#### 3.14.3a. Inspect Agent Pack (pre-import)

Read an `.agentpack` file's manifest WITHOUT importing it — the binding dialog's data source. Returns one row per agent that needs an MCP server, with the server name the manifest references and the caller's server id it currently resolves to (or `null`), plus the caller's MCP servers.

**Endpoint:** `POST /api/v1/agent-packs/inspect`
**Authentication:** Required (JWT or access token)

**Request:** multipart form data — `file` (the `.agentpack`).

**Success Response:**
```json
{
  "status": "success",
  "pack": {"name": "Policing Data Products", "version": "1.0.0", "format_version": "1.4", "profile_count": 4},
  "mcp_requirements": [
    {"tag": "NIBRS", "name": "NIBRS Crime Statistics Analyst", "profile_type": "llm_only",
     "mcp_server_name": "Teradata MCP - nibrs_source_agent",
     "resolved_mcp_server_id": "server-3003a752-…", "resolution": "name"},
    {"tag": "CASEDF", "name": "CASE Digital Evidence Analyst", "profile_type": "llm_only",
     "mcp_server_name": "Teradata MCP - case_source_agent",
     "resolved_mcp_server_id": null, "resolution": null}
  ],
  "mcp_servers": [{"id": "server-3003a752-…", "name": "Teradata MCP - nibrs_source_agent"}]
}
```

`resolution` is `"name"` (auto-matched by the manifest name), `"binding"` (an explicit binding was supplied) or `null` (nothing on this instance matches — the dialog marks the row *not configured here*). An agent that reads several MCP servers adds `additional_servers`: one row per additional server, `{"alias", "mcp_server_name", "resolved_mcp_server_id", "resolution"}` with the same meanings (absent for a single-server agent). An invalid manifest returns `400`. Nothing is written.

---

#### 3.14.4. Export Agent Pack

Export a genie coordinator profile and its sub-profiles as an `.agentpack` file.

**Endpoint:** `POST /api/v1/agent-packs/export`
**Authentication:** Required (JWT or access token)

**Request Body:**
```json
{
  "coordinator_profile_id": "profile_123"
}
```

**Parameters:**
- `coordinator_profile_id` (string, required): ID of genie coordinator profile to export

**Response:** Binary `.agentpack` file download

**Response Headers:**
```
Content-Type: application/zip
Content-Disposition: attachment; filename="Sales_Analytics_Pack.agentpack"
```

**Export Contents:**
- `manifest.json` - Pack metadata, profiles (with `skillsConfig`, `kg_refs`), collection refs, skills refs, knowledge graphs
- `collections/` - All associated RAG collections as `.zip` files
- `skills/` - User skills that are auto-enabled (`active: true`) on any exported profile, as `.skill` zip files (v1.3+ only)
- `knowledge_graphs/` - Active KG per profile as `kg_N.json` files (v1.4+ only)

**Manifest format versions:** `1.1` (base), `1.2` (+ vector store configs), `1.3` (+ skills), `1.4` (+ knowledge graphs). Version is selected automatically.

**Knowledge Graph manifest entry (v1.4):**
```json
{
  "name": "Product Knowledge Graph",
  "kg_id": "kg-abc123",
  "kg_database_name": "ProductDB",
  "owner_profile_tag": "PRODUCT_SME",
  "assigned_profiles": [
    { "tag": "PRODUCT_SME", "is_active": true },
    { "tag": "VAT", "is_active": false }
  ]
}
```
- `owner_profile_tag` - Tag of the profile that owns the KG on the source system (used to resolve owner on import)
- `assigned_profiles` - All profiles assigned to this KG with their active status; on import, assignment rows are created in `kg_profile_assignments`; a profile gets `is_active=true` only if it has no existing active KG already

**KG owner resolution on import (priority order):**
1. `owner_profile_tag` from manifest (when original owner is in this pack)
2. Genie coordinator of the pack (neutral owner for cross-pack KGs)
3. Active-assignment profile (legacy fallback)

**Error Responses:**
```json
// Profile not found
{
  "status": "error",
  "message": "Profile not found or not a genie coordinator"
}

// Not a genie profile
{
  "status": "error",
  "message": "Profile must be a genie coordinator to export as pack"
}
```

**Example:**
```bash
# Export agent pack
curl -X POST http://localhost:5050/api/v1/agent-packs/export \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"coordinator_profile_id": "profile_123"}' \
  --output sales-analytics.agentpack
```

**Use Case:** Share genie configurations with team members or publish to marketplace

---

#### 3.14.5. Create Agent Pack

Create a new agent pack from selected profiles and collections.

**Endpoint:** `POST /api/v1/agent-packs/create`
**Authentication:** Required (JWT or access token)

**Request Body:**
```json
{
  "name": "Customer Analytics Pack",
  "description": "Comprehensive customer data analysis tools",
  "version": "1.0.0",
  "author": "Analytics Team",
  "profile_ids": [1, 2, 3, 4],
  "collection_ids": [5, 6, 7],
  "metadata": {
    "category": "Analytics",
    "tags": ["customer", "analytics", "reporting"]
  }
}
```

**Parameters:**
- `name` (string, required): Pack name (3-100 characters)
- `description` (string, required): Pack description
- `version` (string, required): Semantic version (e.g., "1.0.0")
- `author` (string, optional): Author name
- `profile_ids` (array, required): Profile IDs to include
- `collection_ids` (array, optional): Collection IDs to include
- `metadata` (object, optional): Additional metadata

**Success Response:**
```json
{
  "status": "success",
  "installation_id": 6,
  "pack": {
    "name": "Customer Analytics Pack",
    "version": "1.0.0",
    "profiles_count": 4,
    "collections_count": 3,
    "created_at": "2026-02-06T15:00:00Z"
  }
}
```

---

#### 3.14.6. List Installed Agent Packs

Retrieve all agent packs installed for the current user.

**Endpoint:** `GET /api/v1/agent-packs`
**Authentication:** Required (JWT or access token)

**Success Response:**
```json
{
  "status": "success",
  "agent_packs": [
    {
      "installation_id": 1,
      "name": "Sales Analytics Pack",
      "version": "1.2.0",
      "author": "Analytics Team",
      "description": "Sales data analysis and forecasting tools",
      "profiles_count": 5,
      "collections_count": 4,
      "installed_at": "2026-01-15T10:00:00Z",
      "is_marketplace": false,
      "marketplace_id": null
    },
    {
      "installation_id": 2,
      "name": "Customer Insights Pack",
      "version": "2.0.1",
      "author": "CRM Team",
      "description": "Customer behavior analysis and segmentation",
      "profiles_count": 3,
      "collections_count": 2,
      "installed_at": "2026-02-01T14:30:00Z",
      "is_marketplace": true,
      "marketplace_id": "mp_abc123"
    }
  ],
  "total_packs": 2
}
```

---

#### 3.14.7. Get Agent Pack Details

Retrieve detailed information about a specific agent pack.

**Endpoint:** `GET /api/v1/agent-packs/{installation_id}`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `installation_id` (integer, required): Agent pack installation ID

**Success Response:**
```json
{
  "status": "success",
  "agent_pack": {
    "installation_id": 1,
    "name": "Sales Analytics Pack",
    "version": "1.2.0",
    "author": "Analytics Team",
    "description": "Comprehensive sales data analysis toolkit",
    "installed_at": "2026-01-15T10:00:00Z",
    "profiles": [
      {
        "id": 10,
        "name": "Sales Dashboard",
        "type": "tool_enabled",
        "is_default": false
      },
      {
        "id": 11,
        "name": "Revenue Analysis",
        "type": "rag_focused",
        "is_default": false
      }
    ],
    "collections": [
      {
        "id": 20,
        "name": "Sales Queries",
        "type": "planner",
        "entries_count": 45
      },
      {
        "id": 21,
        "name": "Revenue Metrics",
        "type": "knowledge",
        "entries_count": 120
      }
    ],
    "metadata": {
      "category": "Analytics",
      "tags": ["sales", "analytics", "revenue"],
      "license": "MIT"
    }
  }
}
```

---

#### 3.14.8. Update Agent Pack

Update the editable metadata and/or profile membership of an installed agent pack.

**Endpoint:** `PUT /api/v1/agent-packs/{installation_id}`
**Authentication:** Required (JWT or access token)
**Method:** `PUT`
**Path Parameters:**
- `installation_id` (integer, required) - Agent pack installation ID

**Request Body:**
```json
{
  "name": "VAT Agent Pack v2",
  "description": "Updated description",
  "version": "2.0.0",
  "profile_ids": ["profile-abc123", "profile-def456", "profile-ghi789"]
}
```

**Parameters:**
- `name` (string, required) - New pack name
- `description` (string, optional) - New description
- `version` (string, optional) - New semantic version
- `profile_ids` (array of strings, optional) - Replace the pack's profile membership. Genie children are auto-included; coordinator/expert/standalone roles are resolved automatically. All referenced profiles must already exist.

**Success Response:**
```json
{
  "status": "ok",
  "pack": {
    "installation_id": 3,
    "name": "VAT Agent Pack v2",
    "description": "Updated description",
    "version": "2.0.0",
    "coordinator_tag": "VAT",
    "pack_type": "genie",
    "profiles_count": 10,
    "collections_count": 9
  }
}
```

**Response Fields (when `profile_ids` is provided):**
- `coordinator_tag` (string | null) - Tag of the genie coordinator, if any
- `pack_type` (string) - `"genie"` or `"standalone"`
- `profiles_count` (integer) - Total profiles now referenced by the pack
- `collections_count` (integer) - Total collections now referenced by the pack

**Behavior:**
- Metadata (`name`, `description`, `version`) is updated in `agent_pack_installations` and `manifest_json`
- When `profile_ids` is provided, all existing `agent_pack_resources` rows for this pack are replaced with the new profile/collection set; genie children of any included coordinator are automatically added
- Resources are marked `is_owned=0` (referenced, not owned) — the pack doesn't take ownership of pre-existing profiles

**Error Responses:**
- `400 Bad Request` - Missing `name` or `profile_ids` is an empty list
- `403 Forbidden` / `404 Not Found` - Pack not found or not owned by the current user
- `500 Internal Server Error`

**Example:**
```bash
# Rename a pack and reassign profiles
curl -X PUT http://localhost:5050/api/v1/agent-packs/3 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "VAT Agent Pack v2",
    "version": "2.0.0",
    "profile_ids": ["profile-abc123", "profile-def456"]
  }'
```

---

#### 3.14.9. Publish Agent Pack to Marketplace

Publish an installed agent pack to the marketplace for sharing.

**Endpoint:** `POST /api/v1/agent-packs/{installation_id}/publish`
**Authentication:** Required (JWT or access token with Developer/Enterprise tier)

**Path Parameters:**
- `installation_id` (integer, required): Agent pack installation ID

**Request Body:**
```json
{
  "visibility": "public",
  "category": "Analytics",
  "tags": ["sales", "crm", "reporting"],
  "pricing": "free",
  "targeted_user_emails": []
}
```

**Parameters:**
- `visibility` (string, required): `public` or `private` (default: `private`)
- `category` (string, required): Marketplace category
- `tags` (array, optional): Search tags
- `pricing` (string, optional): `free` or `paid` (default: `free`)
- `targeted_user_emails` (array, optional): Email addresses for private sharing

**Success Response:**
```json
{
  "status": "success",
  "marketplace_id": "mp_abc123",
  "visibility": "public",
  "published_at": "2026-02-06T15:30:00Z",
  "marketplace_url": "https://marketplace.uderia.com/packs/mp_abc123"
}
```

**Authorization:**
- Requires `developer` or `enterprise` tier
- User must be pack owner

---

#### 3.14.10. Browse Marketplace Agent Packs

Browse available agent packs in the marketplace.

**Endpoint:** `GET /api/v1/marketplace/agent-packs`
**Authentication:** Required (JWT or access token)

**Query Parameters:**
- `visibility` (string, optional): which listings to return — `public` (public listings only, **the API default**), `targeted` (listings granted to the caller, plus the caller's own targeted listings), or `all` (the union of the two). A `targeted` listing is **never** returned under `public`, so a caller browsing the default scope does not see a pack shared with them personally — the Uderia marketplace UI therefore sends `all`. An unrecognised value falls back to `public`.
- `category` (string, optional): Filter by category
- `search` (string, optional): Search query
- `sort_by` (string, optional): `popular`, `recent`, `rating` (default: `popular`)
- `limit` (integer, optional): Results per page (default: 20, max: 100)
- `offset` (integer, optional): Pagination offset

> The same `visibility` contract applies to the other marketplace browse endpoints — skills, extensions, knowledge graphs, collections and component instances.

**Per-pack fields used by the card:**
- `source_installation_id` — the publisher's installation. This is the key the trust artefact is stored under, so `GET /v1/trust/artifact/agent_pack/<source_installation_id>` (or the batch form) resolves the publisher's seal. Reads are deliberately not owner-scoped: a consumer must be able to see that the publisher signed.
- `kg_count` — how many knowledge graphs the pack ships, read from the publisher's installation ledger (no republish needed for listings published earlier).
- `profile_tags` / `profile_classes` — the member tags and a `{tag: profile_type}` map, so each member chip renders in its IFOC class colour even for a consumer who does not own those profiles. A tag missing from the map is rendered as unknown, never given a guessed class.

`GET /api/v1/agent-packs` (installed packs) serves the same three facts, plus `installation_id` — the trust key for **your own** copy.

**Success Response:**
```json
{
  "status": "success",
  "packs": [
    {
      "id": "ee2c1a20-44a9-4a65-bc91-70bc296ab737",
      "name": "Policing Data Products",
      "version": "1.0.0",
      "author": "",
      "description": "Two governed AI-Native Data Products …",
      "pack_type": "genie",
      "coordinator_tag": "POLICE",
      "profile_count": 6,
      "collection_count": 2,
      "kg_count": 3,
      "profile_tags": ["PDOCS", "PXPROD", "CASEDF", "NIBRSO", "POLICE", "NIBRS"],
      "profile_classes": {
        "PDOCS": "rag_focused",
        "PXPROD": "llm_only",
        "CASEDF": "llm_only",
        "NIBRSO": "tool_enabled",
        "POLICE": "genie",
        "NIBRS": "llm_only"
      },
      "source_installation_id": 95,
      "has_tool_profiles": true,
      "distribution_mode": "both",
      "visibility": "targeted",
      "publisher_username": "admin",
      "publisher_email": "info@uderia.com",
      "is_publisher": true,
      "is_subscribed": false,
      "is_forked": false,
      "average_rating": 0,
      "rating_count": 0,
      "install_count": 0,
      "download_count": 5,
      "published_at": "2026-09-08T15:11:54.125911+00:00",
      "content_updated_at": "2026-09-08T15:11:54.125911+00:00"
    }
  ],
  "page": 1,
  "total": 6,
  "total_pages": 1
}
```

> `is_publisher` is what lets the UI offer **Manage** instead of **Fork** on your own
> listing; `content_updated_at` (never `published_at`) is the stamp a subscriber
> compares against its own `synced_at` to decide whether an update is available.

---

#### 3.14.11. Install Agent Pack from Marketplace

Install an agent pack from the marketplace.

**Endpoint:** `POST /api/v1/marketplace/agent-packs/{marketplace_id}/install`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `marketplace_id` (string, required): Marketplace pack ID

**Request Body:**
```json
{
  "mcp_server_id": "mcp_server_123",
  "mcp_bindings": {"NIBRS": "server-nibrs-uuid", "CASEDF": "server-case-uuid"},
  "llm_configuration_id": 1,
  "conflict_strategy": "rename"
}
```

`mcp_bindings` (optional) is the subscriber's explicit per-agent map — see 3.14.3 for the precedence and refusal rules; the ids must be the SUBSCRIBER's servers. `GET …/{marketplace_id}/mcp-requirements` (3.14.11a) lists the agents that need one.

**Success Response:**
```json
{
  "status": "success",
  "installation_id": 7,
  "pack_name": "Sales Analytics Pro",
  "profiles_imported": 6,
  "collections_imported": 5,
  "message": "Agent pack installed successfully"
}
```

---

#### 3.14.11b. Sync a Marketplace Agent Pack Subscription

Re-materialize the caller's subscription from the publisher's **live** pack, in place. Profiles are updated under their existing ids (a coordinator's children stay resolved), new tags are added, tags the pack no longer ships are removed (their sessions archived, their knowledge-graph copies deleted), collection references are reconciled and planner references re-keyed to the subscriber's servers, knowledge-graph copies are rebuilt where the publisher's export hash moved (kept otherwise — attestation and activation untouched), the ledger is reconciled, and the publisher's attestations are reinstated where the copies hash identically.

A weaker posture shipped by the publisher is never refused — a subscribed copy is read-only and the publisher's copy is authoritative — it is **reported** in `posture_changes`.

The same function runs for every subscriber, in the background, when the publisher updates the pack in place (3.14.3 `update_in_place`, or a fork route in-place update); a failure there is stamped on that subscriber's installation as `sync_error` and never blocks the publisher.

**Endpoint:** `POST /api/v1/marketplace/agent-packs/{marketplace_id}/sync`
**Authentication:** Required (JWT or access token)

**Request Body (all optional — each overrides the value derived from the subscriber's existing copies):**
```json
{
  "llm_configuration_id": "llm-config-uuid",
  "mcp_server_id": "server-uuid",
  "mcp_bindings": {"NIBRS": "server-nibrs-uuid"}
}
```

**Success Response:**
```json
{
  "status": "synced",
  "name": "Policing Data Products",
  "installation_id": 13,
  "synced_at": "2026-09-07T19:31:02.118+00:00",
  "profiles_updated": 5, "profiles_added": 0, "profiles_dropped": 0, "dropped_tags": [],
  "collections_added": 0, "collections_removed": 0, "planner_keyed": 1,
  "kgs_created": 0, "kgs_rebuilt": 0, "kgs_unchanged": 2, "kgs_removed": 0,
  "skills_recorded": 2, "trust_reinstated": 11,
  "posture_changes": [],
  "warnings": []
}
```

**Errors:** `404` unknown listing or not subscribed · `400` own pack, or a binding/LLM the subscriber does not own · `409` the publisher removed a pack profile (`missing_profiles` names them).

**Read side.** A subscribed listing in 3.14.10 (browse) and a subscribed pack in 3.14.6 (installed list) carry `update_available` (true when the listing's `content_updated_at` is later than the copy's `synced_at`), `synced_at`, `sync_error` and `content_updated_at`. `content_updated_at` moves at publish and on the publisher's in-place update only — `updated_at` is not a content signal, every subscribe and fork bumps it.

**The subscriber's repository references.** A subscribed pack's knowledge and planner repositories are REFERENCES to the publisher's collections (`collection_subscriptions` rows the pack subscribe creates and the pack unsubscribe removes), so one collection id is named by the publisher's installation ledger AND by every subscriber's. `GET /v1/rag/collections` (and `GET /v1/profiles`) serve `agent_packs` scoped to the CALLER's own installations — one chip per pack, never one per account that references the collection. A reference held by one of the caller's pack subscriptions follows the pack: `DELETE /v1/marketplace/subscriptions/<subscription_id>` refuses it with **`409 managed_by_agent_pack`** (`pack_name`, `pack_listing_id`, `pack_installation_id`, and a message naming the pack unsubscribe) BEFORE touching the row; the card reads *Removed with the pack* where Unsubscribe would be. A plain marketplace collection subscription is unchanged.

**The subscriber's knowledge-graph copies.** Each pack graph is materialized as a snapshot-with-sync copy keyed to the PACK listing (`kg_subscriptions.source_kind = 'agent_pack'`, §3.28 addendum in `KG_SUBSCRIPTION_PLAN.md`). `GET /v1/knowledge-graph/list` serves, on such a copy, `source_kind: "agent_pack"`, `pack_name`, `publisher_username`, `pack_synced_at` (the installation's `synced_at`) and an `is_sync_available` computed from the **pack's** stamps — the same comparison as the marketplace card, never the KG-listing hash. A KG-listing copy keeps `source_kind: "kg"` and its listing-hash `is_sync_available`; a graph that is not a subscription carries `source_kind: null`. The copy follows the pack: its card offers **Sync pack** (the pack sync above) and no unsubscribe of its own, and the KG-marketplace sync / unsubscribe (§3.28.11–12) refuse it with `409 managed_by_agent_pack`.

---

#### 3.14.11a. Marketplace Agent Pack MCP Requirements

Per-agent MCP requirements of a listing, resolved for the caller — serves both Subscribe and Fork. Same row shape as 3.14.3a, derived from the publisher's LIVE profiles (the server NAME the materialisation will try to match).

**Endpoint:** `GET /api/v1/marketplace/agent-packs/{marketplace_id}/mcp-requirements`
**Authentication:** Required (JWT or access token)

**Success Response:** `{"status": "success", "pack": {"id": "…", "name": "…"}, "mcp_requirements": [...], "mcp_servers": [...]}` — `404` for an unknown listing.

---

#### 3.14.12. Fork Marketplace Agent Pack

Create a personal copy of a marketplace pack for customization.

**Endpoint:** `POST /api/v1/marketplace/agent-packs/{marketplace_id}/fork`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `marketplace_id` (string, required): Marketplace pack ID to fork

**Request Body:**
```json
{
  "new_name": "My Custom Sales Analytics",
  "mcp_server_id": "mcp_server_123",
  "mcp_bindings": {"NIBRS": "server-nibrs-uuid", "CASEDF": "server-case-uuid"},
  "llm_configuration_id": 1
}
```

`mcp_bindings` (optional): explicit per-agent map, same rules as 3.14.3. The fork runs `conflict_strategy=expand`, so a tag the forker already has is renamed (`NIBRS` → `NIBRS2`) — the binding follows the renamed tag.

**Success Response:**
```json
{
  "status": "success",
  "installation_id": 8,
  "forked_from": "mp_abc123",
  "new_pack_name": "My Custom Sales Analytics",
  "message": "Pack forked successfully. You can now customize it."
}
```

**Use Case:** Customize marketplace packs without affecting original

**Naming (Aug 2026):** the forked copy **keeps the original pack name** — the
old "`<name> (Fork)`" suffix is gone. Fork-ness is structured data, never a
status smuggled into a display name (it leaked into every surface that prints
pack names verbatim, e.g. a skill's pack-provenance badge). The lineage is
stamped as `_source_marketplace_id` in the installation's manifest and served
on `GET /v1/agent-packs` as `is_fork` + `forked_from_marketplace_id`; the UI
renders a "via fork" chip from those fields.

---

#### 3.14.13. Rate Marketplace Agent Pack

Submit a rating for a marketplace pack.

**Endpoint:** `POST /api/v1/marketplace/agent-packs/{marketplace_id}/rate`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `marketplace_id` (string, required): Marketplace pack ID

**Request Body:**
```json
{
  "rating": 5,
  "review": "Excellent pack! Saved us weeks of setup time."
}
```

**Parameters:**
- `rating` (integer, required): Rating 1-5 stars
- `review` (string, optional): Written review (max 500 characters)

**Success Response:**
```json
{
  "status": "success",
  "rating_submitted": 5,
  "new_average_rating": 4.7,
  "total_ratings": 89
}
```

---

#### 3.14.14. Unpublish Agent Pack from Marketplace

Remove an agent pack from the marketplace (pack owner only).

**Endpoint:** `DELETE /api/v1/marketplace/agent-packs/{marketplace_id}`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `marketplace_id` (string, required): Marketplace pack ID

**Success Response:**
```json
{
  "status": "success",
  "message": "Agent pack unpublished from marketplace",
  "installations_affected": 0
}
```

**Note:** Unpublishing doesn't affect users who already installed the pack.

---

#### 3.14.15. Get Targeted Users for Private Pack

Retrieve list of users who can access a private marketplace pack.

**Endpoint:** `GET /api/v1/marketplace/agent-packs/{marketplace_id}/targeted-users`
**Authentication:** Required (JWT or access token, pack owner only)

**Path Parameters:**
- `marketplace_id` (string, required): Marketplace pack ID

**Success Response:**
```json
{
  "status": "success",
  "targeted_users": [
    {
      "email": "john@company.com",
      "added_at": "2026-01-20T10:00:00Z",
      "has_installed": true
    },
    {
      "email": "jane@company.com",
      "added_at": "2026-01-25T14:30:00Z",
      "has_installed": false
    }
  ],
  "total_users": 2
}
```

---

#### 3.14.16. Update Targeted Users for Private Pack

Update the list of users who can access a private marketplace pack.

**Endpoint:** `PUT /api/v1/marketplace/agent-packs/{marketplace_id}/targeted-users`
**Authentication:** Required (JWT or access token, pack owner only)

**Path Parameters:**
- `marketplace_id` (string, required): Marketplace pack ID

**Request Body:**
```json
{
  "targeted_user_emails": [
    "john@company.com",
    "jane@company.com",
    "bob@partner.com"
  ],
  "action": "add"
}
```

**Parameters:**
- `targeted_user_emails` (array, required): Email addresses
- `action` (string, required): `add` or `remove` or `replace`

**Actions:**
- `add`: Add emails to existing list
- `remove`: Remove emails from list
- `replace`: Replace entire list with new emails

**Success Response:**
```json
{
  "status": "success",
  "targeted_users_count": 3,
  "action": "add",
  "message": "Targeted users updated successfully"
}
```

#### 3.14.17. Notify Upstream Repository Author

Send a one-time professional courtesy email to the author of the open-source repository
that an agent pack was sourced from. Intended for packs built via a git-sourced conversion
pipeline (e.g. `build.py --notify-author`). The endpoint is idempotent from the caller's
perspective — the one-time guard is managed client-side in `.notified.json`.

The underlying email resolution and guard logic lives in the shared helper
`agent_packs/common/notify.py`, which any agent pack build script can import.

**Endpoint:** `POST /api/v1/agent-packs/notify-author`
**Authentication:** Required (JWT or access token)

**Request Body:**
```json
{
  "author_email":  "author@example.com",
  "author_name":   "Jane Doe",
  "repo_url":      "https://github.com/example-org/your-source-framework",
  "repo_name":     "your-source-framework",
  "pack_name":     "Your Agent Pack",
  "pack_tags":     ["teradata", "data-product", "design"],
  "license_name":  "MIT"
}
```

**Parameters:**
- `author_email` (string, required): Recipient email address
- `author_name` (string, optional): Author display name used in greeting (default: "there")
- `repo_url` (string, required): Source repository URL shown in email and used for attribution link
- `repo_name` (string, optional): Short repo name shown in email (default: last path segment of `repo_url`)
- `pack_name` (string, optional): Agent pack name shown in email (default: "Uderia Agent Pack")
- `pack_tags` (array, optional): Tag list rendered as chips in email
- `license_name` (string, optional): SPDX license identifier shown in email (default: "Open Source")

**Success Response (200):**
```json
{
  "status": "sent",
  "to": "author@example.com"
}
```

**Error Responses:**
```json
{ "status": "error", "message": "author_email is required" }          // 400
{ "status": "error", "message": "repo_url is required" }              // 400
{ "status": "error", "message": "Email service failed to send..." }   // 500
```

**Email content:**
- Dark header with Uderia brand visuals (`#F15F22` orange accent)
- Pack details box: name, source URL, license, tags
- One-paragraph description of the Uderia platform
- Attribution note with direct link back to the source repository
- "Explore Uderia" CTA button linking to `https://uderia.com`
- Footer with one-time notification disclaimer

**Prerequisites:** `EMAIL_PROVIDER` and its corresponding credentials must be set in the
Uderia server environment:

| Provider | Required env vars |
|----------|------------------|
| SendGrid | `EMAIL_PROVIDER=sendgrid`, `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL` |
| SMTP     | `EMAIL_PROVIDER=smtp`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` |
| AWS SES  | `EMAIL_PROVIDER=aws_ses`, `AWS_SES_FROM_EMAIL`, `AWS_SES_REGION` |

**Typical CLI usage (via `agent_packs/common/notify.py`):**

```bash
# Auto-resolve author email (GitHub profile → git log fallback)
python agent_packs/<pack>/build.py --notify-author

# Supply email directly — bypasses GitHub / git-log resolution entirely
python agent_packs/<pack>/build.py --notify-author --author-email author@example.com

# Test with your own address (delete guard first)
rm -f agent_packs/<pack>/.notified.json
python agent_packs/<pack>/build.py --notify-author --author-email you@example.com \
  --url http://localhost:5050 --password '<pw>'
```

---

#### 3.14.18. Notify Upstream Repository Author — Update

Send a one-time update notification email to the author of a source repository whose adapted
Uderia agent pack has been updated to a new commit. Sent once per source commit; the one-time
guard is managed client-side in `.update_notified.json`.

The underlying logic lives in `agent_packs/common/notify.py` (`notify_author_update()`).

**Endpoint:** `POST /api/v1/agent-packs/notify-author-update`
**Authentication:** Required (JWT or access token)

**Request Body:**
```json
{
  "author_email":    "author@example.com",
  "author_name":     "Jane Doe",
  "repo_url":        "https://github.com/example-org/your-source-framework",
  "repo_name":       "your-source-framework",
  "pack_name":       "Your Agent Pack",
  "pack_tags":       ["@PACK", "@PACK-BUILD", "@PACK-REVIEW"],
  "license_name":    "MIT",
  "previous_commit": "abc1234",
  "current_commit":  "def5678",
  "changes":         ["abc1234 Fix column mapping in builder prompt", "def5678 Add observability module"]
}
```

**Parameters:**
- `author_email` (string, required): Recipient email address
- `author_name` (string, optional): Author display name used in greeting (default: "there")
- `repo_url` (string, required): Source repository URL
- `repo_name` (string, optional): Short repo name (default: last path segment of `repo_url`)
- `pack_name` (string, optional): Agent pack name (default: "Uderia Agent Pack")
- `pack_tags` (array, optional): Tag list rendered as chips in email
- `license_name` (string, optional): SPDX license identifier (default: "Open Source")
- `previous_commit` (string, optional): Previous notified commit hash — shown as `abc1234 → def5678`
- `current_commit` (string, optional): New commit hash
- `changes` (array, optional): Commit messages / change descriptions shown as a bullet list

**Success Response (200):**
```json
{ "status": "sent", "to": "author@example.com" }
```

**Error Responses:**
```json
{ "status": "error", "message": "author_email is required" }          // 400
{ "status": "error", "message": "repo_url is required" }              // 400
{ "status": "error", "message": "Email service failed to send..." }   // 500
```

**Email content:**
- Dark header with Uderia brand visuals
- Update details box: pack name, source URL, commit range, tags
- "What changed" section: bullet list of commit messages
- Attribution note with link back to source repository
- "Explore Uderia" CTA button linking to `https://uderia.com`
- Footer: "Update notifications are sent once per release"

**Comparison with `notify-author`:**

| | `notify-author` | `notify-author-update` |
|---|---|---|
| Purpose | First-deployment introduction | Per-commit update |
| Guard | `.notified.json` (once ever) | `.update_notified.json` (per commit) |
| Content | Platform intro + pack details | Commit range + change list |

**Typical CLI usage:**

```bash
# Auto-resolve email, fires once per commit
python agent_packs/<pack>/build.py --notify-update

# Supply email directly
python agent_packs/<pack>/build.py --notify-update --author-email author@example.com

# Test with your own address (delete guard first)
rm -f agent_packs/<pack>/.update_notified.json
python agent_packs/<pack>/build.py --notify-update --author-email you@example.com \
  --url http://localhost:5050 --password '<pw>'
```

---

### Update Repository Settings

**Endpoint:** `PATCH /api/v1/knowledge/repositories/<id>`

Updates mutable settings of a knowledge repository the caller owns. Any subset of:

| Field | Type | Description |
|---|---|---|
| `sync_interval` | string | `hourly` · `6h` · `daily` · `weekly` |
| `next_sync_at` | ISO 8601 / null | Schedule anchor; advances by one interval after each scheduled sync |
| `source_root` | string / null | Base directory for relative `file://` URIs (null = auto-detect) |
| `embedding_model_locked` | 0/1 | Lock the embedding model |
| `description` | string | Free text |
| `sensitivity` | `public` · `internal` · `confidential` · null | Governance label |
| `sync_auto_ack_findings` | 0/1 | **Standing policy** — after a sync that changed content, acknowledge new injection findings on the changed documents automatically (each one an inbox event) |
| `sync_auto_resign` | 0/1 | **Standing policy** — after a sync that changed content, renew the repository's signature, the signatures of the agents that bind it and the seal of each installed pack that ships them, then carry the renewal to the packs' subscribers (`fan_out_pack_sync` — the same fan-out as `POST /v1/agent-packs/<id>/sync-subscribers`, which reinstates the renewed attestations onto each subscriber's copy by content hash). Renewal only: never a first signature or seal; the owner's signing authority applies; the validator and the pack's aggregate gate still decide; recorded as the `renew` attestation event; the carry as `signature_renewal_carried` |

```bash
curl -X PATCH "http://localhost:5050/api/v1/knowledge/repositories/2" \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"sync_auto_ack_findings": 1, "sync_auto_resign": 1}'
# → {"status": "ok", "updated": {"sync_auto_ack_findings": 1, "sync_auto_resign": 1}}
```

The sync result (`POST …/sync` and the scheduled run) carries what the policy did under
`renewal`: `acknowledged[]`, `knowledge{signed, reason, note}`, `agents[{tag, signed, reason}]`.

### 3.15. Knowledge Repository Management

Knowledge repositories store reference documents and domain knowledge that can be retrieved during query execution. Documents are chunked and embedded for semantic search via the Vector Store Abstraction Layer (ChromaDB, Teradata, or Qdrant Cloud).

**Key Features:**
- Upload documents (PDF, TXT, DOCX, MD)
- Configurable chunking strategies (fixed_size, semantic, recursive)
- Semantic search with similarity scoring
- Document management (list, delete, retrieve chunks)
- Integration with RAG-focused profiles

---

#### 3.15.1. Preview Document Chunking

Preview how a document will be chunked before uploading to a knowledge repository.

**Endpoint:** `POST /api/v1/knowledge/preview-chunking`
**Authentication:** Required (JWT or access token)

**Request Body:**
```json
{
  "file_content": "Base64-encoded file content",
  "file_name": "technical_manual.pdf",
  "chunking_strategy": "recursive",
  "chunk_size": 1000,
  "chunk_overlap": 200
}
```

**Parameters:**
- `file_content` (string, required): Base64-encoded document content
- `file_name` (string, required): Original filename for format detection
- `chunking_strategy` (string, optional): `fixed_size`, `semantic`, or `recursive` (default: `recursive`)
- `chunk_size` (integer, optional): Target chunk size in characters (default: 1000)
- `chunk_overlap` (integer, optional): Overlap between chunks in characters (default: 200)

**Success Response:**
```json
{
  "status": "success",
  "preview": {
    "total_chunks": 45,
    "chunking_strategy": "recursive",
    "chunk_size": 1000,
    "chunk_overlap": 200,
    "sample_chunks": [
      {
        "chunk_id": 0,
        "content": "# Introduction\n\nThis technical manual covers...",
        "length": 982,
        "metadata": {
          "source": "technical_manual.pdf",
          "page": 1
        }
      },
      {
        "chunk_id": 1,
        "content": "## Installation\n\nTo install the system...",
        "length": 1045,
        "metadata": {
          "source": "technical_manual.pdf",
          "page": 2
        }
      }
    ],
    "file_metadata": {
      "file_name": "technical_manual.pdf",
      "file_size": 52480,
      "pages": 15
    }
  }
}
```

**Use Case:** Test chunking settings before uploading large document sets

---

#### 3.15.2. Upload Document to Repository

Upload a document to a knowledge repository with automatic chunking and embedding.

**Endpoint:** `POST /api/v1/knowledge/repositories/<id>/documents`
**Authentication:** Required (JWT or access token)

**Request:** Multipart form data

**Form Fields:**
- `file` (file, required): Document file to upload
- `chunking_strategy` (string, optional): `fixed_size`, `semantic`, or `recursive` (default: `recursive`)
- `chunk_size` (integer, optional): Target chunk size in characters (default: 1000)
- `chunk_overlap` (integer, optional): Overlap between chunks (default: 200)
- `metadata` (JSON string, optional): Additional metadata for the document

**Supported Formats:**
- PDF (`.pdf`)
- Text (`.txt`)
- Markdown (`.md`)
- Word Documents (`.docx`)

**Success Response:**
```json
{
  "status": "success",
  "document": {
    "document_id": "doc_abc123",
    "file_name": "technical_manual.pdf",
    "file_size": 52480,
    "pages": 15,
    "chunks_created": 45,
    "chunking_strategy": "recursive",
    "uploaded_at": "2026-02-06T14:30:00Z"
  },
  "repository_id": "repo_123"
}
```

**Error Responses:**
```json
// Unsupported file format
{
  "status": "error",
  "message": "Unsupported file format. Allowed: pdf, txt, docx, md"
}

// File too large
{
  "status": "error",
  "message": "File size exceeds maximum allowed size (50MB)"
}

// Repository not found
{
  "status": "error",
  "message": "Knowledge repository not found"
}
```

**Example:**
```bash
# Upload PDF document
curl -X POST "http://localhost:5050/api/v1/knowledge/repositories/repo_123/documents" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@technical_manual.pdf" \
  -F "chunking_strategy=recursive" \
  -F "chunk_size=1000" \
  -F "chunk_overlap=200"
```

**Client Behavior:**
- Show upload progress bar (multipart upload)
- Display chunking progress: "Processing: 45/45 chunks created"
- Show success message: "Document uploaded successfully (45 chunks)"

---

#### 3.15.3. List Documents in Repository

Retrieve all documents in a knowledge repository.

**Endpoint:** `GET /api/v1/knowledge/repositories/<id>/documents`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `id` (string, required): Repository ID

**Success Response:**
```json
{
  "status": "success",
  "repository_id": "repo_123",
  "documents": [
    {
      "document_id": "doc_abc123",
      "file_name": "technical_manual.pdf",
      "file_size": 52480,
      "pages": 15,
      "chunks_count": 45,
      "chunking_strategy": "recursive",
      "uploaded_at": "2026-02-06T14:30:00Z",
      "last_accessed": "2026-02-06T15:00:00Z"
    },
    {
      "document_id": "doc_def456",
      "file_name": "user_guide.docx",
      "file_size": 24320,
      "pages": 8,
      "chunks_count": 22,
      "chunking_strategy": "semantic",
      "uploaded_at": "2026-02-05T10:00:00Z",
      "last_accessed": null
    }
  ],
  "total_documents": 2,
  "total_chunks": 67
}
```

**Use Case:** Display document library in UI

---

#### 3.15.4. Delete Document from Repository

Remove a document and all its chunks from a knowledge repository.

**Endpoint:** `DELETE /api/v1/knowledge/repositories/<id>/documents/<doc_id>`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `id` (string, required): Repository ID
- `doc_id` (string, required): Document ID to delete

**Success Response:**
```json
{
  "status": "success",
  "message": "Document deleted successfully",
  "document_id": "doc_abc123",
  "chunks_deleted": 45
}
```

**Client Behavior:**
- Show confirmation modal: "Delete 'technical_manual.pdf'? This will remove 45 chunks."
- After deletion, show: "Document deleted (45 chunks removed)"

---

#### 3.15.5. Search Knowledge Repository

Perform semantic search across all documents in a knowledge repository.

**Endpoint:** `POST /api/v1/knowledge/repositories/<id>/search`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `id` (string, required): Repository ID

**Request Body:**
```json
{
  "query": "How do I configure SSL certificates?",
  "top_k": 5,
  "min_similarity": 0.6
}
```

**Parameters:**
- `query` (string, required): Search query
- `top_k` (integer, optional): Number of results to return (default: 3, max: 20)
- `min_similarity` (float, optional): Minimum similarity score (0.0-1.0, default: 0.6)

**Success Response:**
```json
{
  "status": "success",
  "query": "How do I configure SSL certificates?",
  "results": [
    {
      "chunk_id": "chunk_789",
      "document_id": "doc_abc123",
      "document_name": "technical_manual.pdf",
      "content": "## SSL Certificate Configuration\n\nTo configure SSL certificates, follow these steps:\n1. Generate a certificate signing request (CSR)...",
      "similarity": 0.92,
      "metadata": {
        "page": 12,
        "section": "Security Configuration"
      }
    },
    {
      "chunk_id": "chunk_456",
      "document_id": "doc_abc123",
      "document_name": "technical_manual.pdf",
      "content": "SSL/TLS certificates should be renewed every 90 days...",
      "similarity": 0.78,
      "metadata": {
        "page": 14,
        "section": "Maintenance"
      }
    }
  ],
  "total_results": 2,
  "search_metadata": {
    "min_similarity": 0.6,
    "top_k": 5,
    "chunks_searched": 67
  }
}
```

**Use Case:** Test repository before integrating with RAG-focused profile

---

#### 3.15.6. Get Document Chunks

Retrieve all chunks for a specific document in a knowledge collection.

**Endpoint:** `GET /api/v1/knowledge/collections/<id>/chunks`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `id` (string, required): Collection ID

**Query Parameters:**
- `document_id` (string, optional): Filter chunks by document ID
- `page` (integer, optional): Page number for pagination (default: 1)
- `limit` (integer, optional): Chunks per page (default: 50, max: 200)
- `suspicious_only` (boolean, optional): Return only chunks flagged by the deterministic corpus screen (injection patterns, PII shapes, exfiltration-shaped URLs — the same detectors as the ingest firewall). Requires the guardrails master switch to be on (400 otherwise). The flagged set is screened live (bounded at 20,000 chunks; `scan_capped: true` when the bound was hit) and paginated with `limit`/`offset`.

**Chunk-level screening annotation:** when the guardrails master switch is on, every returned chunk that the corpus screen flags carries a `screen` field — `{"injection": ["<pattern-label>", …], "pii": {"<category>": count, …}, "exfil_urls": n}`. Labels and counts only; matched values are never returned. The response also carries `screening_enabled` (whether annotation ran) and `scan_capped`. The same `screen` field appears on the single-chunk endpoint below. The chunk browser in the repository Inspect view renders these as problem tags, with a **Suspicious only** filter toggle and an **Export findings** JSON download.

**Success Response:**
```json
{
  "status": "success",
  "collection_id": "col_789",
  "chunks": [
    {
      "chunk_id": "chunk_123",
      "document_id": "doc_abc123",
      "content": "# Introduction\n\nThis technical manual...",
      "metadata": {
        "source": "technical_manual.pdf",
        "page": 1,
        "section": "Introduction"
      },
      "embedding": null
    }
  ],
  "pagination": {
    "current_page": 1,
    "total_pages": 3,
    "total_chunks": 145,
    "limit": 50
  }
}
```

**Use Case:** Debug chunking issues, export document content

---

#### 3.15.7. Get Specific Chunk Details

Retrieve detailed information about a specific chunk.

**Endpoint:** `GET /api/v1/knowledge/collections/<id>/chunks/<chunk_id>`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `id` (string, required): Collection ID
- `chunk_id` (string, required): Chunk ID

**Query Parameters:**
- `include_embedding` (boolean, optional): Include embedding vector (default: false)

**Success Response:**
```json
{
  "status": "success",
  "chunk": {
    "chunk_id": "chunk_123",
    "document_id": "doc_abc123",
    "document_name": "technical_manual.pdf",
    "content": "## SSL Certificate Configuration\n\nTo configure SSL certificates...",
    "length": 982,
    "metadata": {
      "source": "technical_manual.pdf",
      "page": 12,
      "section": "Security Configuration",
      "chunk_index": 23
    },
    "created_at": "2026-02-06T14:30:15Z",
    "embedding": null
  }
}
```

**With Embedding:**
```json
{
  "status": "success",
  "chunk": {
    ...other fields...,
    "embedding": [0.023, -0.145, 0.678, ..., 0.234],
    "embedding_model": "text-embedding-ada-002",
    "embedding_dimension": 1536
  }
}
```

**Use Case:** Inspect chunk quality, debug embedding issues

---

### 3.16. LLM Configuration Management

Manage LLM provider configurations including API keys, models, and settings. The system supports multiple providers simultaneously and encrypts all credentials.

**Supported Providers:**
- Google (Gemini 2.0)
- Anthropic (Claude)
- OpenAI (GPT-4o)
- Azure OpenAI
- AWS Bedrock
- Friendli.AI (serverless or dedicated endpoint)
- OpenRouter (unified API for 100+ models)
- Ollama (local, offline)

**Key Features:**
- Encrypted credential storage (Fernet encryption)
- Provider-specific model selection
- Connection testing
- Active configuration management
- Automatic session archiving on deletion

---

#### 3.16.1. List LLM Configurations

Retrieve all LLM configurations for the current user.

**Endpoint:** `GET /api/v1/llm/configurations`
**Authentication:** Required (JWT or access token)

**Success Response:**
```json
{
  "status": "success",
  "configurations": [
    {
      "id": 1,
      "name": "Google Gemini Production",
      "provider": "google",
      "model": "gemini-2.0-flash-exp",
      "is_active": true,
      "created_at": "2025-11-25T10:00:00Z",
      "last_used": "2026-02-06T14:30:00Z"
    },
    {
      "id": 2,
      "name": "Claude Opus for Complex Queries",
      "provider": "anthropic",
      "model": "claude-opus-4-6",
      "is_active": false,
      "created_at": "2025-12-01T12:00:00Z",
      "last_used": "2026-01-15T09:00:00Z"
    }
  ],
  "active_configuration_id": 1,
  "total_configurations": 2
}
```

**Response Fields:**
- `is_active`: Whether this is the currently active LLM configuration
- `last_used`: Last time this configuration was used in a query
- **Security Note**: API keys and credentials are never returned

---

#### 3.16.2. Create LLM Configuration

Add a new LLM provider configuration.

**Endpoint:** `POST /api/v1/llm/configurations`
**Authentication:** Required (JWT or access token)

**Request Body (Google):**
```json
{
  "name": "Google Gemini Production",
  "provider": "google",
  "model": "gemini-2.0-flash-exp",
  "credentials": {
    "api_key": "AIzaSyD..."
  },
  "settings": {
    "temperature": 0.7,
    "max_tokens": 8192
  }
}
```

**Request Body (Anthropic):**
```json
{
  "name": "Claude Sonnet",
  "provider": "anthropic",
  "model": "claude-sonnet-4-5",
  "credentials": {
    "api_key": "sk-ant-..."
  },
  "settings": {
    "temperature": 1.0,
    "max_tokens": 8192
  }
}
```

**Request Body (Azure OpenAI):**
```json
{
  "name": "Azure GPT-4o",
  "provider": "azure",
  "model": "gpt-4o",
  "credentials": {
    "api_key": "your-azure-api-key",
    "endpoint": "https://your-resource.openai.azure.com/",
    "api_version": "2024-02-15-preview",
    "deployment_name": "gpt-4o-deployment"
  },
  "settings": {
    "temperature": 0.7
  }
}
```

**Request Body (AWS Bedrock):**
```json
{
  "name": "Bedrock Claude",
  "provider": "aws",
  "model": "anthropic.claude-v2",
  "credentials": {
    "aws_access_key_id": "AKIA...",
    "aws_secret_access_key": "your-secret-key",
    "region": "us-east-1"
  }
}
```

**Request Body (Friendli.AI - Serverless):**
```json
{
  "name": "Friendli Qwen3 Serverless",
  "provider": "Friendli",
  "model": "Qwen/Qwen3-235B-A22B",
  "credentials": {
    "friendli_token": "flp_..."
  },
  "settings": {
    "temperature": 0.7,
    "max_tokens": 8192
  }
}
```

**Request Body (Friendli.AI - Dedicated Endpoint):**
```json
{
  "name": "Friendli Qwen3 Dedicated",
  "provider": "Friendli",
  "model": "Qwen/Qwen3-235B-A22B",
  "credentials": {
    "friendli_token": "flp_...",
    "friendli_endpoint_url": "https://serving.friendli.ai/dedicated/..."
  },
  "settings": {
    "temperature": 0.7,
    "max_tokens": 8192
  }
}
```

> **Dedicated endpoint:** When `friendli_endpoint_url` is set, the client connects to that specific deployment instead of the shared serverless tier. The URL is automatically normalized to end with `/v1` for OpenAI-compatible routing. Dedicated endpoints do not support connection testing during creation (connection tested implicitly on first use).

**Request Body (OpenRouter):**
```json
{
  "name": "OpenRouter GPT-4o",
  "provider": "OpenRouter",
  "model": "openai/gpt-4o",
  "credentials": {
    "openrouter_api_key": "sk-or-..."
  },
  "settings": {
    "temperature": 0.7,
    "max_tokens": 8192
  }
}
```

> **OpenRouter:** Routes requests to `https://openrouter.ai/api/v1` using the OpenAI-compatible SDK. Model names use the `provider/model-name` format (e.g., `anthropic/claude-3-5-sonnet`, `meta-llama/llama-3.1-70b-instruct`). See [openrouter.ai/models](https://openrouter.ai/models) for the full list.

**Request Body (Ollama - Local):**
```json
{
  "name": "Ollama Llama3 Local",
  "provider": "ollama",
  "model": "llama3",
  "credentials": {
    "base_url": "http://localhost:11434"
  }
}
```

**Success Response:**
```json
{
  "status": "success",
  "configuration": {
    "id": 3,
    "name": "Google Gemini Production",
    "provider": "google",
    "model": "gemini-2.0-flash-exp",
    "is_active": false,
    "created_at": "2026-02-06T14:35:00Z"
  },
  "message": "LLM configuration created successfully"
}
```

**Optional Fields (any provider):**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `judge_panel_enabled` | boolean | absent = `true` | May this model sit on the **LLM judge panel**? Set `false` to exclude it — the model still answers normally. One panel serves **both** judge layers, so this governs both: the judge a **grounding gate escalates to in flight** (whose ruling can withhold an answer) and the panel that **scores answer quality after the turn** — plus the manual "Score this turn" and the eval CLI, since all four resolve through the same selection path. |
| `thinking_budget` | integer \| `null` | absent/`null` = the model's own default | Google only — Gemini 2.5+ thinking tokens (`0` disabled, `-1` dynamic). Absent **or `null`** sends no thinking config at all. Send `null` to CLEAR a stored value: the update path shallow-merges, so an omitted key leaves the previous number in place. **`0` is best-effort** — see the note below. |

> **Why is `thinking_budget: 0` only best-effort?** Some Gemini models reject a *disabled*
> budget outright with `400 INVALID_ARGUMENT` while accepting every other value, and the
> capability is **not discoverable** — `GET /v1beta/models/<id>` reports `"thinking": true`
> for a model that accepts `0` and one that refuses it alike. Measured 2026-09-09,
> `gemini-3.5-flash-lite`, `gemini-3.6-flash`, `gemini-3.1-pro-preview` and
> `gemini-flash-lite-latest` refuse it while `gemini-2.5-flash-lite`, `gemini-3.1-flash-lite`,
> `gemini-3.5-flash` and `gemini-3.7/3.8-flash` accept it — scattered, so no version or family
> rule expresses it. The platform learns the refusal from the API and runs such a model with
> thinking **enabled** rather than failing the call (disabling thinking is an optimisation,
> never a correctness requirement), so the setting is silently not honoured for them and a
> WARNING names the model. Nothing to change in your request.

> **Why exclude a model from the panel?** The panel waits for every member on every judged
> turn, so a slow or unresponsive model sets the latency of the whole evaluation while
> contributing no vote. Exclusion is a deliberate operator decision, never automatic.
> **Absent means participating**, so configurations created before this field existed are
> unaffected; only an explicit `false` opts out. Exclusion is per *configuration* — another
> config of the same provider family can still be selected. Excluding every configuration
> leaves an empty panel and judges simply abstain.

**Validation Rules:**
- `name`: 3-100 characters, must be unique for user
- `provider`: Must be one of supported providers
- `model`: Must be valid for selected provider
- `credentials.api_key`: Required for cloud providers (automatically encrypted)

**Client Behavior:**
- API key input field shows masked characters: `••••••••••••••••`
- After creation, show: "Configuration created. Test connection before use."
- Credentials encrypted with Fernet before storage

---

#### 3.16.3. Get LLM Configuration Details

Retrieve detailed information about a specific LLM configuration.

**Endpoint:** `GET /api/v1/llm/configurations/<id>`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `id` (integer, required): Configuration ID

**Success Response:**
```json
{
  "status": "success",
  "configuration": {
    "id": 1,
    "name": "Google Gemini Production",
    "provider": "google",
    "model": "gemini-2.0-flash-exp",
    "is_active": true,
    "settings": {
      "temperature": 0.7,
      "max_tokens": 8192
    },
    "created_at": "2025-11-25T10:00:00Z",
    "updated_at": "2026-01-10T14:00:00Z",
    "last_used": "2026-02-06T14:30:00Z",
    "usage_stats": {
      "total_queries": 1247,
      "total_tokens_input": 2456789,
      "total_tokens_output": 1234567,
      "total_cost": 45.67
    }
  }
}
```

**Security Note:** Credentials (API keys) are never returned in GET requests. To update credentials, use PUT endpoint.

---

#### 3.16.4. Update LLM Configuration

Update an existing LLM configuration (name, model, settings, or credentials).

**Endpoint:** `PUT /api/v1/llm/configurations/<id>`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `id` (integer, required): Configuration ID

**Request Body (Partial Update):**
```json
{
  "name": "Google Gemini Production v2",
  "model": "gemini-2.0-pro-exp",
  "settings": {
    "temperature": 0.8
  }
}
```

**Request Body (Update Credentials):**
```json
{
  "credentials": {
    "api_key": "new-api-key-here"
  }
}
```

**Request Body (Exclude from / restore to the judge panel):**
```json
{ "judge_panel_enabled": false }
```

> This is a partial update, so send the field explicitly to change it in **either**
> direction — sending `true` is what restores a previously excluded model. Omitting the
> key leaves the stored value as it is (it does not reset to the default).

**Success Response:**
```json
{
  "status": "success",
  "message": "LLM configuration updated successfully",
  "configuration": {
    "id": 1,
    "name": "Google Gemini Production v2",
    "provider": "google",
    "model": "gemini-2.0-pro-exp",
    "updated_at": "2026-02-06T14:40:00Z"
  }
}
```

**Client Behavior:**
- If credentials updated, show: "Credentials updated. Test connection to verify."
- If model changed, show warning: "Model changed. Active sessions may be affected."

---

#### 3.16.5. Delete LLM Configuration

Delete an LLM configuration and archive all sessions using it.

**Endpoint:** `DELETE /api/v1/llm/configurations/<id>`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `id` (integer, required): Configuration ID

**Success Response:**
```json
{
  "status": "success",
  "message": "LLM configuration deleted successfully",
  "sessions_archived": 15
}
```

**Error Responses:**
```json
// Configuration is in use by profiles
{
  "status": "error",
  "message": "Cannot delete LLM configuration in use by 3 profiles. Update profiles first."
}

// Configuration is active
{
  "status": "error",
  "message": "Cannot delete active LLM configuration. Activate another configuration first."
}
```

**Client Behavior:**
- **Before deletion:** Check if configuration is used by profiles
- **Show confirmation:** "Delete configuration? 15 sessions will be archived."
- **Session archiving:** All sessions using this LLM configuration are automatically archived
- **Archived reason:** "LLM configuration '{name}' was deleted"
- **After deletion:** Sessions list refreshes, archived sessions hidden by default

**Safety Rules:**
- Cannot delete active configuration (must activate another first)
- Cannot delete if used by profiles (must update profiles first)
- All sessions using configuration are archived (not deleted)

---

#### 3.16.6. Activate LLM Configuration

Set an LLM configuration as the active configuration for the current user.

**Endpoint:** `POST /api/v1/llm/configurations/<id>/activate`
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `id` (integer, required): Configuration ID to activate

**Success Response:**
```json
{
  "status": "success",
  "message": "LLM configuration activated",
  "configuration": {
    "id": 2,
    "name": "Claude Opus for Complex Queries",
    "provider": "anthropic",
    "model": "claude-opus-4-6",
    "is_active": true
  },
  "previous_active_id": 1
}
```

**Client Behavior:**
- Previous active configuration automatically deactivated
- Show success message: "Configuration '{name}' is now active"
- New sessions use this configuration by default

---

#### 3.16.7. Test LLM Configuration

Test connectivity and validate credentials for an LLM configuration.

**Endpoint:** `POST /api/v1/llm_configurations/<id>/test` (note the underscore — this route is not under `/llm/configurations`)
**Authentication:** Required (JWT or access token)

**Path Parameters:**
- `id` (string, required): Configuration ID to test

**Success Response:**
```json
{
  "status": "success",
  "test_results": {
    "connection": "success",
    "model_available": true,
    "model": "gemini-2.0-flash-exp",
    "response_time_ms": 347,
    "test_query": "Respond with 'OK'",
    "test_response": "OK",
    "tokens_used": {
      "input": 5,
      "output": 2
    }
  },
  "message": "Connection test successful"
}
```

**Error Responses:**
```json
// Invalid API key
{
  "status": "error",
  "test_results": {
    "connection": "failed",
    "error": "Authentication failed: Invalid API key"
  },
  "message": "Connection test failed"
}

// Model not available
{
  "status": "error",
  "test_results": {
    "connection": "success",
    "model_available": false,
    "error": "Model 'gpt-4o' not found or not accessible"
  },
  "message": "Model not available"
}

// Network error
{
  "status": "error",
  "test_results": {
    "connection": "failed",
    "error": "Connection timeout after 10 seconds"
  },
  "message": "Connection test failed"
}
```

**Test Procedure:**
1. Decrypts stored API key
2. Sends test query to provider: "Respond with 'OK'"
3. Verifies response received
4. Checks model availability
5. Measures response time

**Client Behavior:**
- Show loading indicator during test: "Testing connection..."
- Success: Green checkmark + "Connection successful (347ms)"
- Failure: Red X + error message with retry button

**Use Case:** Verify configuration before setting as active

---

### 3.17. Consumption & Analytics

Track token usage, costs, and query analytics across sessions and users. The system provides detailed breakdowns by provider, model, profile, and time period.

**Key Features:**
- Real-time token counting from LLM responses
- Cost calculation with provider-specific pricing
- Per-user and system-wide analytics
- Historical data with time-based filtering
- Turn-by-turn consumption breakdown

---

#### 3.17.1. Get User Consumption Summary

Retrieve consumption summary for the current user.

**Endpoint:** `GET /api/v1/consumption/summary`
**Authentication:** Required (JWT or access token)

**Query Parameters:**
- `start_date` (string, optional): Filter from date (ISO 8601: `2026-01-01T00:00:00Z`)
- `end_date` (string, optional): Filter to date (ISO 8601: `2026-02-06T23:59:59Z`)
- `profile_id` (integer, optional): Filter by specific profile

**Success Response:**
```json
{
  "status": "success",
  "user_uuid": "123e4567-e89b-12d3-a456-426614174000",
  "summary": {
    "total_prompts": 347,
    "total_tokens": {
      "input": 1245678,
      "output": 876543,
      "total": 2122221
    },
    "total_cost": 12.34,
    "currency": "USD",
    "date_range": {
      "start": "2026-01-01T00:00:00Z",
      "end": "2026-02-06T23:59:59Z"
    }
  },
  "breakdown_by_provider": [
    {
      "provider": "google",
      "prompts": 200,
      "tokens": {"input": 800000, "output": 500000, "total": 1300000},
      "cost": 6.50,
      "models_used": ["gemini-2.0-flash-exp", "gemini-2.0-pro-exp"]
    },
    {
      "provider": "anthropic",
      "prompts": 147,
      "tokens": {"input": 445678, "output": 376543, "total": 822221},
      "cost": 5.84,
      "models_used": ["claude-sonnet-4-5", "claude-opus-4-6"]
    }
  ],
  "breakdown_by_profile": [
    {
      "profile_id": 1,
      "profile_name": "Default SQL Agent",
      "prompts": 250,
      "tokens": {"input": 950000, "output": 650000, "total": 1600000},
      "cost": 9.12
    },
    {
      "profile_id": 2,
      "profile_name": "Research Assistant",
      "prompts": 97,
      "tokens": {"input": 295678, "output": 226543, "total": 522221},
      "cost": 3.22
    }
  ]
}
```

**Use Case:** User dashboard showing consumption metrics

---

#### 3.17.2. Get System-Wide Consumption Summary (Admin)

Retrieve consumption summary for all users (admin only).

**Endpoint:** `GET /api/v1/consumption/system-summary`
**Authentication:** Required (JWT or access token with admin tier)

**Query Parameters:**
- `start_date` (string, optional): Filter from date
- `end_date` (string, optional): Filter to date

**Success Response:**
```json
{
  "status": "success",
  "system_summary": {
    "total_users": 24,
    "total_prompts": 5847,
    "total_tokens": {
      "input": 23456789,
      "output": 18765432,
      "total": 42222221
    },
    "total_cost": 256.78,
    "currency": "USD",
    "date_range": {
      "start": "2026-01-01T00:00:00Z",
      "end": "2026-02-06T23:59:59Z"
    }
  },
  "top_consumers": [
    {
      "user_uuid": "123e4567-...",
      "username": "data_analyst_john",
      "prompts": 1247,
      "tokens_total": 8456789,
      "cost": 62.34
    },
    {
      "user_uuid": "789e4567-...",
      "username": "research_team",
      "prompts": 987,
      "tokens_total": 6234567,
      "cost": 45.67
    }
  ],
  "breakdown_by_provider": [
    {
      "provider": "google",
      "prompts": 3200,
      "tokens_total": 25000000,
      "cost": 145.00
    },
    {
      "provider": "anthropic",
      "prompts": 2647,
      "tokens_total": 17222221,
      "cost": 111.78
    }
  ]
}
```

**Authorization:**
- Requires `admin` tier
- Returns 403 Forbidden for non-admin users

---

#### 3.17.3. Get All Users Consumption Data (Admin)

Retrieve detailed consumption data for all users (admin only).

**Endpoint:** `GET /api/v1/consumption/users`
**Authentication:** Required (JWT or access token with admin tier)

**Query Parameters:**
- `threshold` (float, optional): Only return users whose monthly quota usage is at or above this percentage (e.g., `80.0`)
- `sort` (string, optional): Sort field (default: `total_tokens`). `total_tokens` orders by **all-time tokens consumed** (archive + current period — what the admin table displays), applied in SQL before pagination; `period_tokens` orders by the current monthly counter; `success_rate` and `cost` as named
- `limit` (integer, optional): Number of users to return (default: 100)
- `offset` (integer, optional): Pagination offset (default: 0)

**Success Response:**
```json
{
  "users": [
    {
      "user_id": "123e4567-...",
      "username": "data_analyst_john",
      "email": "john@company.com",
      "is_admin": false,
      "current_period": "2026-08",
      "total_input_tokens": 4567890,
      "total_output_tokens": 388889,
      "total_tokens": 4956779,
      "input_tokens_limit": null,
      "output_tokens_limit": null,
      "input_tokens_remaining": null,
      "output_tokens_remaining": null,
      "input_tokens_today": 120000,
      "output_tokens_today": 8000,
      "tokens_today": 128000,
      "lifetime_input_tokens": 26146623,
      "lifetime_output_tokens": 358574,
      "lifetime_total_tokens": 26505197,
      "successful_turns": 700,
      "failed_turns": 39,
      "total_turns": 739
    }
  ],
  "total_count": 24,
  "limit": 100,
  "offset": 0
}
```

**Field semantics (three token scopes, do not conflate):**
- `total_*_tokens` — the **current monthly period** (what quota limits and `Usage %` are measured against; resets at monthly rollover, prior periods archived to `consumption_periods_archive`)
- `*_tokens_today` — the **current UTC day** (resets at midnight UTC; self-healed lazily on any user activity plus swept by the platform daily job and the startup catch-up)
- `lifetime_*_tokens` — **all time**, computed as `SUM(consumption_periods_archive) + current period`. Deliberately not summed from `consumption_turns`: that ledger is pruned to 90 days at each monthly rollover, so a turn-ledger sum silently shrinks over time. The admin "Tokens Consumed" column reads these fields.

**Use Case:** Admin dashboard for monitoring user consumption and quota enforcement

---

#### 3.17.4. Get Consumption by Turns

Retrieve consumption broken down by individual conversation turns.

**Endpoint:** `GET /api/v1/consumption/turns`
**Authentication:** Required (JWT or access token)

**Query Parameters:**
- `session_id` (string, optional): Filter by specific session
- `start_date` (string, optional): Filter from date
- `end_date` (string, optional): Filter to date
- `limit` (integer, optional): Number of turns to return (default: 100, max: 500)

**Success Response:**
```json
{
  "status": "success",
  "turns": [
    {
      "turn_id": "turn_abc123",
      "session_id": "session_456",
      "profile_id": 1,
      "profile_name": "Default SQL Agent",
      "provider": "google",
      "model": "gemini-2.0-flash-exp",
      "user_query": "Show me all products with low inventory",
      "timestamp": "2026-02-06T14:30:00Z",
      "tokens": {
        "input": 3456,
        "output": 2345,
        "total": 5801
      },
      "cost": 0.042,
      "duration_ms": 2347,
      "success": true
    },
    {
      "turn_id": "turn_def456",
      "session_id": "session_789",
      "profile_id": 2,
      "profile_name": "Research Assistant",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "user_query": "Summarize this technical document",
      "timestamp": "2026-02-06T13:15:00Z",
      "tokens": {
        "input": 8934,
        "output": 1234,
        "total": 10168
      },
      "cost": 0.156,
      "duration_ms": 4567,
      "success": true
    }
  ],
  "total_turns": 2,
  "summary": {
    "total_tokens": 15969,
    "total_cost": 0.198
  }
}
```

**Use Case:** Detailed consumption audit, identify expensive queries

---

#### 3.17.5. Get Consumption History

Retrieve historical consumption data with time-based aggregation.

**Endpoint:** `GET /api/v1/consumption/history`
**Authentication:** Required (JWT or access token)

**Query Parameters:**
- `start_date` (string, required): Start date (ISO 8601)
- `end_date` (string, required): End date (ISO 8601)
- `group_by` (string, optional): Aggregation interval (`hour`, `day`, `week`, `month`) (default: `day`)
- `timezone` (string, optional): Timezone for aggregation (default: `UTC`)

**Success Response:**
```json
{
  "status": "success",
  "history": [
    {
      "period": "2026-02-01",
      "prompts": 45,
      "tokens": {"input": 125678, "output": 98765, "total": 224443},
      "cost": 1.23,
      "sessions": 12
    },
    {
      "period": "2026-02-02",
      "prompts": 67,
      "tokens": {"input": 189012, "output": 145678, "total": 334690},
      "cost": 1.89,
      "sessions": 18
    },
    {
      "period": "2026-02-03",
      "prompts": 52,
      "tokens": {"input": 142345, "output": 112890, "total": 255235},
      "cost": 1.45,
      "sessions": 15
    }
  ],
  "total_periods": 6,
  "aggregation": {
    "group_by": "day",
    "timezone": "UTC"
  },
  "totals": {
    "prompts": 347,
    "tokens": {"input": 1245678, "output": 876543, "total": 2122221},
    "cost": 12.34
  }
}
```

**Use Case:** Generate consumption charts, trend analysis

---

#### 3.17.6. Get Session Analytics (Already Documented)

(See section 3.11.1 for full details)

**Endpoint:** `GET /api/v1/sessions/analytics`
**Authentication:** Required (JWT or access token)

**Brief Summary:**
Returns comprehensive analytics across all sessions including:
- Total sessions, tokens, success rate
- Average tokens per session
- Model usage breakdown
- Profile usage statistics

---

#### 3.17.7. Get User Quota Status

Retrieve current user's quota usage and limits.

**Endpoint:** `GET /api/v1/auth/user/quota-status`
**Authentication:** Required (JWT or access token)

**Success Response:**
```json
{
  "status": "success",
  "user_uuid": "123e4567-e89b-12d3-a456-426614174000",
  "tier": "developer",
  "quota": {
    "prompts_per_month": 2000,
    "prompts_used_this_month": 347,
    "prompts_remaining": 1653,
    "usage_percentage": 17.35,
    "reset_date": "2026-03-01T00:00:00Z",
    "days_until_reset": 22
  },
  "limits": {
    "max_tokens_per_query": 100000,
    "max_concurrent_sessions": 10,
    "rate_limit_per_hour": 100
  },
  "warnings": []
}
```

**With Warnings:**
```json
{
  ...other fields...,
  "warnings": [
    {
      "type": "approaching_limit",
      "message": "You have used 90% of your monthly prompt quota",
      "severity": "warning"
    }
  ]
}
```

**Quota Exceeded:**
```json
{
  "status": "error",
  "error": "quota_exceeded",
  "message": "Monthly prompt quota exceeded (2000/2000 used)",
  "quota": {
    "prompts_per_month": 2000,
    "prompts_used_this_month": 2000,
    "prompts_remaining": 0,
    "usage_percentage": 100.0,
    "reset_date": "2026-03-01T00:00:00Z"
  }
}
```

**Quota Tiers:**

| Tier | Prompts/Month | Max Tokens/Query | Concurrent Sessions |
|------|---------------|------------------|---------------------|
| Free | 100 | 50,000 | 3 |
| User | 500 | 75,000 | 5 |
| Developer | 2,000 | 100,000 | 10 |
| Enterprise | 10,000 | 200,000 | 50 |
| Unlimited | ∞ | 500,000 | ∞ |

**Client Behavior:**
- Display quota usage in header: "347 / 2000 prompts used (17%)"
- Show warning when > 80%: "Warning: 90% of quota used"
- Block new queries when quota exceeded: "Quota exceeded. Resets on March 1."

---

#### 3.17.8. Get User Consumption Summary (Alternate Endpoint)

**Endpoint:** `GET /api/v1/auth/user/consumption-summary`
**Authentication:** Required (JWT or access token)

**Note:** This endpoint provides similar functionality to `GET /api/v1/consumption/summary` but may have slight differences in response format. Prefer using `/api/v1/consumption/summary` for consistency.

**Success Response:**
```json
{
  "status": "success",
  "consumption": {
    "total_prompts": 347,
    "total_tokens_input": 1245678,
    "total_tokens_output": 876543,
    "total_cost": 12.34,
    "last_updated": "2026-02-06T14:30:00Z"
  }
}
```

---

#### 3.17.9. Cost Tracking Implementation Details

The platform tracks costs using a two-tier pricing system:

**1. LiteLLM Pricing Database:**
- Automatically syncs model pricing from LiteLLM's database
- Covers major providers (Google, Anthropic, OpenAI, Azure, AWS)
- Updated via `POST /api/v1/costs/sync` endpoint

**2. Manual Overrides:**
- Override pricing for specific models
- Set custom pricing for private/local models
- Fallback pricing for unknown models

**Cost Calculation:**
```python
input_cost = (input_tokens / 1_000_000) * model_input_price_per_1m
output_cost = (output_tokens / 1_000_000) * model_output_price_per_1m
total_cost = input_cost + output_cost
```

**Token Counting:**
- Tokens extracted from LLM provider responses (not pre-estimated)
- Per-turn and per-session accumulation
- Stored in database for historical analysis

**See Also:**
- Section 3.18 (Phase 2): Cost Management API endpoints
- `src/trusted_data_agent/core/cost_manager.py` for implementation

---

#### 3.17.10. Analytics Dashboard Data Flow

**Client-Side Flow:**
1. **Page Load:** Fetch `/api/v1/consumption/summary` and `/api/v1/consumption/history`
2. **Render Charts:** Use history data to render consumption trends
3. **Display Stats:** Show total prompts, tokens, cost from summary
4. **Poll for Updates:** Refresh every 60 seconds for real-time data

**Server-Side Flow:**
1. **Query Execution:** Tokens extracted from LLM response metadata
2. **Cost Calculation:** Apply pricing from `llm_model_costs` table
3. **Database Insert:** Store consumption data in `consumption_records` table
4. **Aggregation:** Analytics endpoints aggregate from `consumption_records`

**Performance Optimization:**
- Consumption data pre-aggregated by day for faster queries
- Indexes on `user_uuid`, `timestamp`, `provider` columns
- Caching of frequently accessed summaries (5-minute TTL)

---

### 3.18. Cost Management

Manage LLM pricing configurations, sync from LiteLLM, and analyze cost trends. Admin-only operations.

#### 3.18.1. Sync Costs from LiteLLM

**Endpoint:** `POST /api/v1/costs/sync`

**Authentication:** Admin only

**Description:** Syncs model pricing data from the LiteLLM pricing database and optionally checks model availability via LLM provider APIs.

**LiteLLM Integration:**
- Automatically fetches latest pricing from LiteLLM's model registry
- Handles provider-specific pricing variations (Google, Anthropic, OpenAI, Azure, etc.)
- Preserves manual overrides (entries with `is_manual_override: true`)
- Updates deprecated status based on provider API responses

**Request Body:**
```json
{
  "check_availability": true  // Optional, default: true
}
```

**Request Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `check_availability` | boolean | No | Check model availability via provider APIs (default: true) |

**Response (200 OK):**
```json
{
  "status": "success",
  "pricing": {
    "synced_count": 127,
    "new_models": 5,
    "updated_models": 12
  },
  "availability": {
    "checked": true,
    "deprecated_count": 3,
    "undeprecated_count": 1,
    "skipped_providers": ["Azure", "Ollama"]
  },
  "warnings": [
    "Failed to check availability for provider 'Azure': No API key configured"
  ]
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `pricing.synced_count` | integer | Total number of models synced from LiteLLM |
| `pricing.new_models` | integer | Count of newly discovered models |
| `pricing.updated_models` | integer | Count of models with updated pricing |
| `availability.deprecated_count` | integer | Models marked as deprecated (not returned by provider API) |
| `availability.undeprecated_count` | integer | Previously deprecated models now available again |
| `availability.skipped_providers` | array | Providers skipped due to missing credentials |
| `warnings` | array | Non-fatal errors during sync (e.g., API failures) |

**Example:**
```bash
curl -X POST http://localhost:5050/api/v1/costs/sync \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"check_availability": true}'
```

**Manual Override Preservation:**

When syncing from LiteLLM, entries marked as manual overrides are **not updated**:
- Allows custom pricing for private LLM deployments
- Prevents overwriting organization-specific negotiated rates
- Use `PUT /api/v1/costs/models/<cost_id>` to update manual entries

**Use Cases:**
1. **Regular maintenance**: Sync weekly to get latest pricing updates
2. **After adding LLM config**: Sync to get pricing for new provider
3. **Troubleshooting costs**: Re-sync to fix missing/incorrect pricing

**Error Responses:**

| Status Code | Description | Example Response |
|-------------|-------------|------------------|
| 401 | Not authenticated | `{"status": "error", "message": "JWT token missing"}` |
| 403 | Not admin user | `{"status": "error", "message": "Admin access required"}` |
| 500 | Sync failed | `{"status": "error", "message": "LiteLLM API unavailable"}` |

---

#### 3.18.2. Get All Model Costs

**Endpoint:** `GET /api/v1/costs/models`

**Authentication:** Admin only

**Description:** Retrieves all model pricing entries from the database, including provider, model name, input/output costs, and metadata.

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `include_fallback` | boolean | No | `true` | Include fallback entry for unknown models |

**Response (200 OK):**
```json
{
  "status": "success",
  "count": 127,
  "costs": [
    {
      "id": "google_gemini-2.0-flash-001",
      "provider": "Google",
      "model": "gemini-2.0-flash-001",
      "input_cost_per_million": 0.075,
      "output_cost_per_million": 0.30,
      "is_manual_override": false,
      "is_deprecated": false,
      "notes": "Synced from LiteLLM",
      "updated_at": "2026-02-06T10:30:00Z"
    },
    {
      "id": "anthropic_claude-opus-4-6",
      "provider": "Anthropic",
      "model": "claude-opus-4-6",
      "input_cost_per_million": 15.0,
      "output_cost_per_million": 75.0,
      "is_manual_override": true,
      "is_deprecated": false,
      "notes": "Custom enterprise pricing",
      "updated_at": "2026-01-15T14:20:00Z"
    },
    {
      "id": "FALLBACK",
      "provider": "FALLBACK",
      "model": "FALLBACK",
      "input_cost_per_million": 10.0,
      "output_cost_per_million": 30.0,
      "is_manual_override": false,
      "is_deprecated": false,
      "notes": "Default pricing for unknown models",
      "updated_at": "2025-12-01T08:00:00Z"
    }
  ]
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier (format: `{provider}_{model}`) |
| `provider` | string | LLM provider name (Google, Anthropic, OpenAI, etc.) |
| `model` | string | Model identifier (e.g., `gemini-2.0-flash-001`) |
| `input_cost_per_million` | float | Cost per 1M input tokens in USD |
| `output_cost_per_million` | float | Cost per 1M output tokens in USD |
| `is_manual_override` | boolean | Whether this is a manually added/modified entry |
| `is_deprecated` | boolean | Whether the model is no longer available |
| `notes` | string | Optional notes about pricing source or changes |
| `updated_at` | string (ISO 8601) | Last update timestamp |

**Example:**
```bash
curl -X GET "http://localhost:5050/api/v1/costs/models?include_fallback=true" \
  -H "Authorization: Bearer $JWT"
```

**Filtering:**

To exclude the fallback entry (useful for UI dropdowns):
```bash
curl -X GET "http://localhost:5050/api/v1/costs/models?include_fallback=false" \
  -H "Authorization: Bearer $JWT"
```

**Use Cases:**
1. **Cost auditing**: Review all model pricing before budget planning
2. **UI population**: Load pricing for cost calculator interfaces
3. **Manual override verification**: Identify which entries have custom pricing

---

#### 3.18.3. Update Model Cost (Manual Override)

**Endpoint:** `PUT /api/v1/costs/models/{cost_id}`

**Authentication:** Admin only

**Description:** Updates pricing for a specific model entry. Marks the entry as a manual override to prevent LiteLLM sync from overwriting it.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `cost_id` | string | Cost entry ID (format: `{provider}_{model}`) |

**Request Body:**
```json
{
  "input_cost": 0.080,
  "output_cost": 0.35,
  "notes": "Updated from official Google pricing page 2026-02-06"
}
```

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `input_cost` | float | Yes | Cost per 1M input tokens (USD) |
| `output_cost` | float | Yes | Cost per 1M output tokens (USD) |
| `notes` | string | No | Optional notes about the override reason |

**Response (200 OK):**
```json
{
  "status": "success",
  "message": "Model cost updated"
}
```

**Example:**
```bash
curl -X PUT http://localhost:5050/api/v1/costs/models/google_gemini-2.0-flash-001 \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "input_cost": 0.080,
    "output_cost": 0.35,
    "notes": "Updated from official docs"
  }'
```

**Behavior:**
- Sets `is_manual_override: true` on the entry
- LiteLLM sync will **not** overwrite this entry in future syncs
- To revert to LiteLLM pricing: delete the entry and re-sync

**Use Cases:**
1. **Private LLM deployments**: Set custom pricing for internal models
2. **Negotiated rates**: Override with organization-specific pricing
3. **Pricing corrections**: Fix incorrect LiteLLM data
4. **Cost modeling**: Test different pricing scenarios

**Error Responses:**

| Status Code | Description | Example Response |
|-------------|-------------|------------------|
| 400 | Missing required fields | `{"status": "error", "message": "input_cost and output_cost are required"}` |
| 404 | Cost entry not found | `{"status": "error", "message": "Model cost entry not found"}` |

---

#### 3.18.4. Add Manual Model Cost

**Endpoint:** `POST /api/v1/costs/models`

**Authentication:** Admin only

**Description:** Adds a new pricing entry for a model not in the LiteLLM database (e.g., private deployments, custom models, or preview models).

**Request Body:**
```json
{
  "provider": "Google",
  "model": "gemini-2.5-flash-preview",
  "input_cost": 0.075,
  "output_cost": 0.30,
  "notes": "Preview model pricing from beta program"
}
```

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | string | Yes | Provider name (must match LLM config provider) |
| `model` | string | Yes | Model identifier |
| `input_cost` | float | Yes | Cost per 1M input tokens (USD) |
| `output_cost` | float | Yes | Cost per 1M output tokens (USD) |
| `notes` | string | No | Optional notes about pricing source |

**Response (201 Created):**
```json
{
  "status": "success",
  "cost_id": "google_gemini-2.5-flash-preview",
  "message": "Model cost added"
}
```

**Example:**
```bash
curl -X POST http://localhost:5050/api/v1/costs/models \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "Google",
    "model": "gemini-2.5-flash-preview",
    "input_cost": 0.075,
    "output_cost": 0.30,
    "notes": "Preview model pricing"
  }'
```

**Behavior:**
- Creates new entry with `is_manual_override: true`
- Entry is protected from LiteLLM sync overwriting
- Cost entry becomes active immediately for cost calculations

**Use Cases:**
1. **Private models**: Add pricing for self-hosted LLMs (Ollama, custom deployments)
2. **Preview models**: Track costs for beta/preview models not yet in LiteLLM
3. **Custom providers**: Add pricing for organization-specific LLM endpoints
4. **Testing**: Add dummy entries for cost calculation testing

**Error Responses:**

| Status Code | Description | Example Response |
|-------------|-------------|------------------|
| 400 | Missing required fields | `{"status": "error", "message": "provider, model, input_cost, and output_cost are required"}` |
| 409 | Entry already exists | `{"status": "error", "message": "Model cost entry already exists"}` |

**Duplicate Prevention:**

If an entry with the same `provider` and `model` already exists:
- POST returns 409 Conflict
- Use `PUT /api/v1/costs/models/{cost_id}` to update instead

---

#### 3.18.5. Delete Model Cost

**Endpoint:** `DELETE /api/v1/costs/models/{cost_id}`

**Authentication:** Admin only

**Description:** Deletes a model pricing entry. Cannot delete the fallback entry (use `PUT /api/v1/costs/fallback` to modify fallback pricing).

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `cost_id` | string | Cost entry ID (format: `{provider}_{model}`) |

**Response (200 OK):**
```json
{
  "status": "success",
  "message": "Model cost deleted"
}
```

**Example:**
```bash
curl -X DELETE http://localhost:5050/api/v1/costs/models/google_gemini-1.5-pro \
  -H "Authorization: Bearer $JWT"
```

**Behavior:**
- Permanently removes the pricing entry from database
- Future cost calculations will use fallback pricing for this model
- To restore LiteLLM pricing: run `POST /api/v1/costs/sync` after deletion

**Protected Entries:**

The following entries **cannot** be deleted:
- `FALLBACK` entry (use `PUT /api/v1/costs/fallback` instead)

**Use Cases:**
1. **Remove deprecated models**: Clean up entries for discontinued models
2. **Revert manual overrides**: Delete custom entry to restore LiteLLM pricing (then sync)
3. **Database cleanup**: Remove test or incorrect entries

**Error Responses:**

| Status Code | Description | Example Response |
|-------------|-------------|------------------|
| 404 | Entry not found or protected | `{"status": "error", "message": "Model cost entry not found or cannot be deleted"}` |

**Client Behavior:**

After deleting a model cost entry:
- Re-fetch the costs list via `GET /api/v1/costs/models`
- If the model is still in use, warn the user that fallback pricing will apply
- Consider syncing from LiteLLM to restore default pricing

---

#### 3.18.6. Update Fallback Cost

**Endpoint:** `PUT /api/v1/costs/fallback`

**Authentication:** Admin only

**Description:** Updates the fallback pricing used for unknown models (models not in the `llm_model_costs` table). This provides a conservative cost estimate when exact pricing is unavailable.

**Request Body:**
```json
{
  "input_cost": 10.0,
  "output_cost": 30.0
}
```

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `input_cost` | float | Yes | Fallback cost per 1M input tokens (USD) |
| `output_cost` | float | Yes | Fallback cost per 1M output tokens (USD) |

**Response (200 OK):**
```json
{
  "status": "success",
  "message": "Fallback cost updated"
}
```

**Example:**
```bash
curl -X PUT http://localhost:5050/api/v1/costs/fallback \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "input_cost": 15.0,
    "output_cost": 45.0
  }'
```

**Default Fallback Values:**

| Pricing Tier | Input Cost | Output Cost | Rationale |
|--------------|------------|-------------|-----------|
| Default | $10.00 | $30.00 | Conservative estimate (Opus-level pricing) |
| Custom | User-defined | User-defined | Organization-specific safety margin |

**When Fallback is Used:**

1. **New preview models**: Model name not yet added to LiteLLM database
2. **Private deployments**: Custom model identifiers (e.g., `ollama/custom-model`)
3. **Typos in model names**: Incorrect model identifier in LLM config
4. **Sync failures**: LiteLLM database temporarily unavailable

**Use Cases:**
1. **Cost safety margins**: Set fallback higher than typical rates to avoid budget surprises
2. **Internal accounting**: Align fallback with organization's average LLM costs
3. **Development environments**: Set low fallback for test/dev models

**Monitoring Fallback Usage:**

Check which models are using fallback pricing:
```bash
# Get cost analytics and look for "FALLBACK" in cost breakdown
curl -X GET http://localhost:5050/api/v1/costs/analytics \
  -H "Authorization: Bearer $JWT" | jq '.cost_by_model.FALLBACK'
```

**Error Responses:**

| Status Code | Description | Example Response |
|-------------|-------------|------------------|
| 400 | Missing required fields | `{"status": "error", "message": "input_cost and output_cost are required"}` |
| 500 | Update failed | `{"status": "error", "message": "Failed to update fallback cost"}` |

---

#### 3.18.7. Get Cost Analytics

**Endpoint:** `GET /api/v1/costs/analytics`

**Authentication:** Admin only

**Description:** Retrieves comprehensive cost analytics across all sessions, including total costs, breakdowns by provider/model, trends over time, and most expensive queries.

**Response (200 OK):**
```json
{
  "total_cost": 247.83,
  "cost_by_provider": {
    "Google": 142.50,
    "Anthropic": 85.33,
    "OpenAI": 15.00,
    "FALLBACK": 5.00
  },
  "cost_by_model": {
    "gemini-2.0-flash-001": 98.40,
    "claude-opus-4-6": 75.20,
    "gemini-2.0-flash-thinking-exp": 44.10,
    "claude-sonnet-4-5": 10.13,
    "gpt-4o": 15.00,
    "FALLBACK": 5.00
  },
  "avg_cost_per_session": 2.48,
  "avg_cost_per_turn": 0.31,
  "most_expensive_sessions": [
    {
      "session_id": "abc123",
      "profile_tag": "FOCUS",
      "total_cost": 12.45,
      "turn_count": 8,
      "created_at": "2026-02-05T10:30:00Z"
    },
    {
      "session_id": "def456",
      "profile_tag": "CHAT",
      "total_cost": 8.92,
      "turn_count": 15,
      "created_at": "2026-02-04T14:20:00Z"
    }
  ],
  "most_expensive_queries": [
    {
      "session_id": "abc123",
      "turn_index": 3,
      "query_preview": "Analyze all customer transactions for...",
      "cost": 3.24,
      "provider": "Anthropic",
      "model": "claude-opus-4-6",
      "timestamp": "2026-02-05T10:45:00Z"
    }
  ],
  "cost_trend": [
    {
      "date": "2026-02-01",
      "total_cost": 45.20,
      "session_count": 12
    },
    {
      "date": "2026-02-02",
      "total_cost": 52.30,
      "session_count": 15
    }
  ]
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `total_cost` | float | Total cost across all sessions (USD) |
| `cost_by_provider` | object | Cost breakdown by LLM provider |
| `cost_by_model` | object | Cost breakdown by specific model |
| `avg_cost_per_session` | float | Average cost per session |
| `avg_cost_per_turn` | float | Average cost per turn (single query/response) |
| `most_expensive_sessions` | array | Top 10 sessions by total cost |
| `most_expensive_queries` | array | Top 10 individual queries by cost |
| `cost_trend` | array | Daily cost aggregation (last 30 days) |

**Example:**
```bash
curl -X GET http://localhost:5050/api/v1/costs/analytics \
  -H "Authorization: Bearer $JWT"
```

**Analytics Calculations:**

**Cost Formula:**
```
cost = (input_tokens / 1,000,000) * input_cost_per_million +
       (output_tokens / 1,000,000) * output_cost_per_million
```

**Session Cost:**
- Sum of all turn costs in the session
- Includes both strategic planning and tactical execution tokens

**Provider/Model Attribution:**
- Uses provider/model from LLM handler response metadata
- Falls back to profile's LLM config if metadata unavailable
- "FALLBACK" category shows costs from unknown models

**Use Cases:**
1. **Budget monitoring**: Track total LLM spending over time
2. **Cost optimization**: Identify expensive queries/sessions for optimization
3. **Provider comparison**: Compare costs across different LLM providers
4. **Financial reporting**: Generate cost reports for accounting/management

**Performance Note:**

Analytics endpoint scans all session workflow files on disk:
- First request may take 5-10 seconds for large deployments (1000+ sessions)
- Results are cached for 5 minutes
- Use `GET /api/v1/consumption/summary` for faster per-user consumption (database-backed)

**Relationship to Consumption Tracking:**

| Endpoint | Data Source | Scope | Performance |
|----------|-------------|-------|-------------|
| `/v1/costs/analytics` | Session files on disk | All sessions (deep analysis) | Slower (file I/O) |
| `/v1/consumption/summary` | Database (`consumption_records`) | Per-user aggregated | Faster (indexed queries) |

---

**Cost Management System Architecture:**

1. **Cost Database (`llm_model_costs` table)**:
   - Stores pricing for all LLM providers/models
   - Synced from LiteLLM + manual overrides
   - Fallback entry for unknown models

2. **LiteLLM Integration**:
   - Automatic pricing sync via `POST /v1/costs/sync`
   - Preserves manual overrides during sync
   - Checks model availability via provider APIs

3. **Cost Calculation Pipeline**:
   ```
   LLM Response → Extract Tokens → Lookup Pricing → Calculate Cost → Store in Consumption Records
   ```

4. **Consumption Records (`consumption_records` table)**:
   - Stores per-turn token/cost data
   - Indexed by user_uuid, timestamp, provider
   - Powers consumption analytics and quota enforcement

5. **Analytics Aggregation**:
   - Real-time: Session files (workflow.json)
   - Pre-aggregated: Database consumption records
   - Hybrid: Cost analytics uses both sources

**Cost Tracking Flow:**

```
1. User submits query
2. LLM handler calls provider API
3. Provider returns response with usage_metadata
4. Extract input_tokens, output_tokens
5. Lookup pricing: cost_manager.get_cost(provider, model)
6. Calculate cost: (tokens / 1M) * cost_per_million
7. Store in consumption_records table
8. Update session workflow.json
9. Emit token_update event to frontend
```

**Manual Override Workflow:**

```
1. Admin identifies incorrect pricing
2. POST /v1/costs/models (add new entry) OR
   PUT /v1/costs/models/{id} (update existing)
3. Entry marked as is_manual_override: true
4. Future syncs preserve this entry
5. Cost calculations use manual pricing
6. To revert: DELETE entry + POST /v1/costs/sync
```

---

### 3.19. Genie Multi-Profile Coordination

Manage Genie profiles that coordinate multiple child profiles to answer complex multi-domain questions. Genie profiles route queries to specialized expert profiles and synthesize their responses.

**Genie Architecture Overview:**

```
User Query → Genie Coordinator (Parent Session)
                ↓
     ┌──────────┼──────────┐
     ↓          ↓           ↓
Child Profile 1  Child Profile 2  Child Profile 3
(SQL Expert)    (Doc Search)     (API Expert)
     ↓          ↓           ↓
   Result 1   Result 2    Result 3
     └──────────┼──────────┘
                ↓
         Synthesized Answer
```

**Key Concepts:**

1. **Parent Session**: Genie profile that orchestrates coordination
2. **Child Sessions** (Slaves): Individual expert profiles invoked by Genie
3. **Primary Classification Profile**: Shared MCP server classification for child inheritance
4. **Coordination Flow**: Query → Route to experts → Collect results → Synthesize response

---

#### 3.19.1. Get Primary Classification Profile

**Endpoint:** `GET /api/v1/config/master-classification-profile`

**Authentication:** Required (JWT or Access Token)

**Description:** Retrieves the primary classification profile configuration. Returns per-server primary profile mappings (each MCP server can have its own primary profile) and legacy single-master profile for backwards compatibility.

**Primary Classification Profile Purpose:**

- **Inheritance**: Child profiles can inherit tool/prompt classification from primary profile
- **Consistency**: Ensures all child profiles use the same MCP server resources
- **Per-Server Flexibility**: Different MCP servers can have different primary profiles
- **Classification Propagation**: Disabled tools/prompts in primary automatically disabled in children

**Response (200 OK):**
```json
{
  "status": "success",
  "master_classification_profile_ids": {
    "postgres-mcp-server": "profile-123-abc",
    "mysql-mcp-server": "profile-456-def"
  },
  "master_classification_profile_id": "profile-123-abc"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `master_classification_profile_ids` | object | Dictionary mapping MCP server IDs to primary profile IDs |
| `master_classification_profile_id` | string | **DEPRECATED**: Legacy single primary profile (for backwards compatibility) |

**Example:**
```bash
curl -X GET http://localhost:5050/api/v1/config/master-classification-profile \
  -H "Authorization: Bearer $JWT"
```

**Per-Server Primary Profiles:**

Each MCP server can have its own primary profile:
- **postgres-mcp-server** → Uses SQL Expert profile as primary
- **mysql-mcp-server** → Uses different SQL profile as primary
- **docs-search-server** → Uses Documentation Expert as primary

**Use Cases:**
1. **Genie setup verification**: Check which profiles are set as primary before creating Genie
2. **Child profile configuration**: Determine which primary to inherit from
3. **Multi-server environments**: Manage different primary profiles per data source

**Migration Note:**

The `master_classification_profile_id` field is **deprecated** but maintained for backwards compatibility:
- **Old behavior** (single master): All child profiles inherited from one global primary
- **New behavior** (per-server): Each MCP server has its own primary profile
- **Migration path**: Update clients to use `master_classification_profile_ids` dictionary
- **Removal target**: Q2 2026

---

#### 3.19.2. Set Primary Classification Profile

**Endpoint:** `PUT /api/v1/config/master-classification-profile`

**Authentication:** Required (JWT or Access Token)

**Description:** Sets a profile as the primary classification profile for a specific MCP server. The primary profile must be `tool_enabled` type (not `llm_only`) and have an MCP server configured.

**Validation Rules:**

1. **Profile Type**: Must be `tool_enabled` (uses MCP tools)
2. **MCP Server**: Profile must have `mcpServerId` configured
3. **Not LLM-Only**: Cannot use `llm_only` or `rag_focused` profiles
4. **Profile Exists**: Profile must belong to the authenticated user

**Request Body:**
```json
{
  "profile_id": "profile-123-abc"
}
```

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `profile_id` | string | Yes | Profile ID to set as primary classification profile |

**Response (200 OK):**
```json
{
  "status": "success",
  "message": "Primary classification profile set successfully"
}
```

**Response (400 Bad Request - Validation Failure):**
```json
{
  "status": "error",
  "message": "Profile must be tool_enabled type with an MCP server configured"
}
```

**Example:**
```bash
curl -X PUT http://localhost:5050/api/v1/config/master-classification-profile \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"profile_id": "profile-123-abc"}'
```

**Side Effects:**

When primary classification profile is changed:

1. **Active Profile Inheritance Update**: If currently active profile inherits classification, `APP_STATE` is updated immediately
2. **Disabled Tools/Prompts Recalculation**: Child profiles re-inherit from new primary
3. **Resource Panel Refresh**: Frontend resource panel shows updated enabled/disabled state
4. **Context Regeneration**: MCP contexts regenerated to reflect new classification

**Example Workflow:**

```bash
# 1. Get current primary profile
CURRENT=$(curl -s -X GET http://localhost:5050/api/v1/config/master-classification-profile \
  -H "Authorization: Bearer $JWT" | jq -r '.master_classification_profile_id')

echo "Current primary: $CURRENT"

# 2. Set new primary profile
curl -X PUT http://localhost:5050/api/v1/config/master-classification-profile \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"profile_id": "profile-456-def"}'

# 3. Verify change
NEW=$(curl -s -X GET http://localhost:5050/api/v1/config/master-classification-profile \
  -H "Authorization: Bearer $JWT" | jq -r '.master_classification_profile_id')

echo "New primary: $NEW"
```

**Use Cases:**
1. **Genie coordinator setup**: Set SQL Expert as primary before creating SQL-focused Genie
2. **Environment switching**: Change primary when switching between dev/staging/prod data sources
3. **Resource access control**: Change primary to enable/disable specific MCP tools for all children

**Error Responses:**

| Status Code | Description | Example Response |
|-------------|-------------|------------------|
| 400 | Profile validation failed | `{"status": "error", "message": "Profile must be tool_enabled type"}` |
| 401 | Not authenticated | `{"status": "error", "message": "Authentication required"}` |
| 404 | Profile not found | `{"status": "error", "message": "Profile not found"}` |

---

#### 3.19.3. Execute Genie Coordinated Query

**Endpoint:** `POST /api/v1/sessions/{session_id}/genie-query`

**Authentication:** Required (JWT or Access Token)

**Description:** Submits a query to a Genie coordinator profile, which routes the question to multiple child profiles, collects their results, and synthesizes a comprehensive answer. This is the primary execution endpoint for Genie multi-profile coordination.

**Genie Execution Flow:**

```
1. User submits query to Genie parent session
2. Coordinator LLM analyzes query complexity
3. Determine which child profiles to invoke (routing)
4. Create child sessions (one per selected child profile)
5. Submit query to each child session via REST API
6. Poll child sessions for completion
7. Collect all child results
8. Coordinator synthesizes final answer
9. Return synthesized response to user
```

**Request Body:**
```json
{
  "prompt": "What are the top 3 products by revenue and what do customers say about them in reviews?"
}
```

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | Yes | User query to be routed to child profiles |

**Response (202 Accepted):**
```json
{
  "task_id": "task_20260206_103045_abc123",
  "status": "processing",
  "message": "Genie coordination started"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Unique task identifier for polling via `GET /api/v1/tasks/{task_id}` |
| `status` | string | Task status: `processing` (initial), `completed`, `error` |
| `message` | string | Human-readable status message |

**Example:**
```bash
# Submit query to Genie session
TASK_RESPONSE=$(curl -X POST http://localhost:5050/api/v1/sessions/$SESSION_ID/genie-query \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What are the top products and customer sentiment?"}')

TASK_ID=$(echo "$TASK_RESPONSE" | jq -r '.task_id')
echo "Task ID: $TASK_ID"

# Poll for results
sleep 5
curl -X GET "http://localhost:5050/api/v1/tasks/$TASK_ID" \
  -H "Authorization: Bearer $JWT"
```

**Genie Coordination Events:**

During execution, the task emits real-time events via the notification channel:

| Event Type | Description | Payload |
|------------|-------------|---------|
| `genie_coordination_start` | Coordination begins | `{"child_profile_count": 3}` |
| `genie_routing_decision` | Child profiles selected | `{"selected_profiles": ["SQL Expert", "Review Analyzer"]}` |
| `genie_child_session_created` | Child session spawned | `{"child_session_id": "...", "profile_tag": "SQL"}` |
| `genie_child_query_submitted` | Query sent to child | `{"child_session_id": "...", "child_task_id": "..."}` |
| `genie_child_result_received` | Child completed | `{"child_session_id": "...", "success": true}` |
| `genie_synthesis_start` | Synthesizing results | `{"result_count": 2}` |
| `genie_coordination_complete` | Final answer ready | `{"total_duration": 12.4}` |

**Poll Results:**

```bash
# Get full task results with events
curl -X GET "http://localhost:5050/api/v1/tasks/$TASK_ID" \
  -H "Authorization: Bearer $JWT" | jq '{
    status: .status,
    events: .events | map(select(.event_type | startswith("genie_"))),
    result: .result
  }'
```

**Genie Configuration Requirements:**

Before using this endpoint, ensure the Genie profile has:

1. **Profile Type**: `profile_type: "genie"`
2. **Child Profiles**: `genieConfig.slaveProfiles: [...]` (at least 1 child)
3. **LLM Config**: `llmConfigurationId` for coordinator LLM
4. **Child Validation**: All child profiles must exist and be accessible

**Error Responses:**

| Status Code | Description | Example Response |
|-------------|-------------|------------------|
| 400 | Not a Genie profile | `{"error": "This endpoint is only for genie profiles. Use /v1/sessions/{session_id}/query for other profile types."}` |
| 400 | No child profiles configured | `{"error": "Genie profile has no child profiles configured."}` |
| 400 | Missing prompt | `{"error": "The 'prompt' field is required."}` |
| 401 | Not authenticated | `{"error": "Authentication required"}` |
| 404 | Session not found | `{"error": "Session 'abc123' not found."}` |

**Use Cases:**
1. **Multi-domain questions**: Questions requiring SQL + document search + API calls
2. **Complex analysis**: Combine data retrieval with sentiment analysis
3. **Cross-functional queries**: Route parts of query to different specialized profiles

**Routing Strategies:**

Genie coordinator can use different routing strategies:

| Strategy | Description | When to Use |
|----------|-------------|-------------|
| **Consult All** | Query all child profiles | Comprehensive analysis needed |
| **Smart Routing** | LLM selects relevant children | Efficiency over completeness |
| **Sequential** | Children run in order | Dependencies between profiles |
| **Parallel** | Children run simultaneously | No dependencies, fastest execution |

The routing strategy is configured in `genieConfig.routingStrategy` (profile configuration).

---

#### 3.19.4. Get Child Sessions

**Endpoint:** `GET /api/v1/sessions/{session_id}/slaves`

**Authentication:** Required (JWT or Access Token)

**Description:** Retrieves all child sessions spawned by a Genie parent session. Returns metadata about each child including profile, status, and nesting level.

**Response (200 OK):**
```json
{
  "parent_session_id": "session-genie-parent-abc123",
  "slave_count": 3,
  "slaves": [
    {
      "session_id": "session-child-sql-expert-def456",
      "slave_profile_id": "profile-sql-expert-123",
      "slave_profile_tag": "SQL_EXPERT",
      "nesting_level": 1,
      "created_at": "2026-02-06T10:30:15Z",
      "status": "active"
    },
    {
      "session_id": "session-child-doc-search-ghi789",
      "slave_profile_id": "profile-doc-search-456",
      "slave_profile_tag": "DOC_SEARCH",
      "nesting_level": 1,
      "created_at": "2026-02-06T10:30:18Z",
      "status": "active"
    },
    {
      "session_id": "session-child-api-expert-jkl012",
      "slave_profile_id": "profile-api-expert-789",
      "slave_profile_tag": "API_EXPERT",
      "nesting_level": 1,
      "created_at": "2026-02-06T10:30:20Z",
      "status": "active"
    }
  ]
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `parent_session_id` | string | Genie parent session ID (echoed from request) |
| `slave_count` | integer | Number of child sessions spawned |
| `slaves` | array | List of child session objects |
| `slaves[].session_id` | string | Child session unique identifier |
| `slaves[].slave_profile_id` | string | Profile ID used for this child |
| `slaves[].slave_profile_tag` | string | Profile tag (e.g., "SQL_EXPERT") |
| `slaves[].nesting_level` | integer | Nesting depth (1 = direct child, 2 = grandchild, etc.) |
| `slaves[].created_at` | string (ISO 8601) | Child session creation timestamp |
| `slaves[].status` | string | Session status: `active`, `archived` |

**Example:**
```bash
# Get all child sessions for a Genie parent
curl -X GET http://localhost:5050/api/v1/sessions/$GENIE_SESSION_ID/slaves \
  -H "Authorization: Bearer $JWT" | jq '.slaves[] | {profile_tag, status}'
```

**Nesting Levels:**

Genie profiles can spawn nested child sessions:

```
Level 0: User Session (regular profile)
Level 1: Genie Parent Session
Level 2: Child Session (SQL Expert)
Level 3: Grandchild Session (if Child is also a Genie)
```

**Use Cases:**
1. **Coordination tracking**: Monitor which child profiles were invoked
2. **Result aggregation**: Fetch individual child session results
3. **Debugging**: Inspect child session execution details
4. **Session cleanup**: Identify orphaned child sessions for archival

**Example: Fetch Child Session Details**

```bash
# Get child sessions
CHILDREN=$(curl -s -X GET http://localhost:5050/api/v1/sessions/$GENIE_SESSION_ID/slaves \
  -H "Authorization: Bearer $JWT")

# Extract first child session ID
CHILD_ID=$(echo "$CHILDREN" | jq -r '.slaves[0].session_id')

# Get full child session data
curl -X GET http://localhost:5050/api/v1/sessions/$CHILD_ID \
  -H "Authorization: Bearer $JWT" | jq '{
    session_id,
    profile_tag,
    turn_count,
    is_genie_slave: .genie_metadata.is_genie_slave,
    parent_session_id: .genie_metadata.parent_session_id
  }'
```

**Error Responses:**

| Status Code | Description | Example Response |
|-------------|-------------|------------------|
| 401 | Not authenticated | `{"error": "Authentication required"}` |
| 404 | Parent session not found | `{"error": "Session 'abc123' not found."}` |
| 500 | Database error | `{"error": "Failed to retrieve child sessions."}` |

**Child Session Lifecycle:**

1. **Creation**: Child session created when Genie routes query to child profile
2. **Execution**: Child runs independently with its own profile configuration
3. **Completion**: Results stored in child session's conversation.json
4. **Aggregation**: Parent Genie synthesizes child results
5. **Persistence**: Child sessions remain accessible for inspection

**Session Archiving:**

Child sessions are automatically archived when:
- Parent Genie session is deleted
- Child profile is deleted (cascade archiving)
- MCP server used by child profile is removed

---

#### 3.19.5. Get Parent Genie Session

**Endpoint:** `GET /api/v1/sessions/{session_id}/genie-parent`

**Authentication:** Required (JWT or Access Token)

**Description:** For a given session, determines if it's a child session and returns its parent Genie session information. Returns `null` if the session is not a child.

**Response (200 OK - Child Session):**
```json
{
  "is_genie_slave": true,
  "parent_session_id": "session-genie-parent-abc123",
  "nesting_level": 1,
  "slave_profile_tag": "SQL_EXPERT",
  "parent_session": {
    "session_id": "session-genie-parent-abc123",
    "profile_id": "profile-genie-coordinator-xyz",
    "profile_tag": "GENIE_COORDINATOR",
    "created_at": "2026-02-06T10:30:00Z"
  }
}
```

**Response (200 OK - Not a Child Session):**
```json
{
  "is_genie_slave": false,
  "parent_session_id": null
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `is_genie_slave` | boolean | Whether this session is a child of a Genie parent |
| `parent_session_id` | string \| null | Parent Genie session ID (null if not a child) |
| `nesting_level` | integer | Nesting depth (0 = root, 1 = child, 2 = grandchild) |
| `slave_profile_tag` | string | Profile tag used for this child session |
| `parent_session` | object \| null | Parent session metadata |
| `parent_session.session_id` | string | Parent session unique identifier |
| `parent_session.profile_id` | string | Parent profile ID (Genie coordinator) |
| `parent_session.profile_tag` | string | Parent profile tag |
| `parent_session.created_at` | string (ISO 8601) | Parent session creation timestamp |

**Example:**
```bash
# Check if a session is a child
curl -X GET http://localhost:5050/api/v1/sessions/$SESSION_ID/genie-parent \
  -H "Authorization: Bearer $JWT" | jq '{
    is_child: .is_genie_slave,
    parent: .parent_session_id,
    nesting_level
  }'
```

**Use Cases:**
1. **Breadcrumb navigation**: Build session hierarchy for UI navigation
2. **Context understanding**: Determine if session results are child results
3. **Result aggregation**: Trace child results back to parent coordination
4. **Session visualization**: Render parent-child relationships in UI

**Example: Build Session Hierarchy**

```bash
# Recursive function to build full hierarchy
function get_hierarchy() {
  local SESSION_ID=$1

  # Get parent info
  PARENT_INFO=$(curl -s -X GET http://localhost:5050/api/v1/sessions/$SESSION_ID/genie-parent \
    -H "Authorization: Bearer $JWT")

  IS_CHILD=$(echo "$PARENT_INFO" | jq -r '.is_genie_slave')

  if [ "$IS_CHILD" == "true" ]; then
    PARENT_ID=$(echo "$PARENT_INFO" | jq -r '.parent_session_id')
    NESTING=$(echo "$PARENT_INFO" | jq -r '.nesting_level')

    echo "Session $SESSION_ID is child of $PARENT_ID (level $NESTING)"

    # Recurse to find grandparent
    get_hierarchy "$PARENT_ID"
  else
    echo "Session $SESSION_ID is root (no parent)"
  fi
}

# Start from a child session
get_hierarchy "session-child-sql-expert-def456"

# Output:
# Session session-child-sql-expert-def456 is child of session-genie-parent-abc123 (level 1)
# Session session-genie-parent-abc123 is root (no parent)
```

**Genie Metadata Structure:**

Child sessions store parent relationship in `genie_metadata`:

```json
{
  "genie_metadata": {
    "is_genie_slave": true,
    "parent_session_id": "session-genie-parent-abc123",
    "slave_profile_id": "profile-sql-expert-123",
    "created_by_genie": true
  }
}
```

**Error Responses:**

| Status Code | Description | Example Response |
|-------------|-------------|------------------|
| 401 | Not authenticated | `{"error": "Authentication required"}` |
| 404 | Session not found | `{"error": "Session 'abc123' not found."}` |

**UI Integration:**

Frontend can use this endpoint to:
- Show "Part of Genie Coordination" badge on child sessions
- Render breadcrumb: `Parent Genie → SQL Expert (this session)`
- Link child session back to parent for context
- Disable certain UI actions for child sessions (e.g., prevent deletion while parent is active)

---

**Genie System Architecture Overview:**

**Database Schema:**

1. **Profiles Table**: Stores Genie profile configuration
   ```sql
   profile_type: 'genie'
   genieConfig: {
     slaveProfiles: ['profile-id-1', 'profile-id-2'],
     routingStrategy: 'smart' | 'consult_all' | 'sequential',
     synthesisPrompt: 'Custom synthesis instructions...'
   }
   ```

2. **Genie Session Links Table**: Tracks parent-child relationships
   ```sql
   CREATE TABLE genie_session_links (
     parent_session_id TEXT,
     slave_session_id TEXT,
     slave_profile_id TEXT,
     slave_profile_tag TEXT,
     nesting_level INTEGER,
     created_at TEXT
   )
   ```

3. **Session Metadata**: `genie_metadata` field in session JSON
   ```json
   {
     "is_genie_slave": true,
     "parent_session_id": "...",
     "slave_profile_id": "...",
     "created_by_genie": true
   }
   ```

**Execution Flow:**

```
1. User calls POST /v1/sessions/{id}/genie-query
2. Validate session has genie profile type
3. Get child profiles from genieConfig.slaveProfiles
4. Create background task for coordination
5. Coordinator LLM analyzes query
6. Routing decision (which children to invoke)
7. For each selected child:
   a. Create child session via session_manager
   b. Submit query to child via POST /v1/sessions/{child_id}/query
   c. Poll child task for completion
8. Collect all child results
9. Coordinator synthesizes final answer
10. Store result in parent session
11. Emit genie_coordination_complete event
```

**Best Practices:**

1. **Profile Design**: Create focused child profiles (SQL Expert, Document Search, API Expert)
2. **Primary Classification**: Set appropriate primary profile before creating Genie
3. **LLM Selection**: Use powerful LLM for coordinator (Claude Opus, GPT-4) for better routing
4. **Child Limits**: Limit to 3-5 child profiles to avoid coordination overhead
5. **Synthesis Prompts**: Customize synthesis instructions in `genieConfig.synthesisPrompt`

**Performance Considerations:**

| Metric | Value | Notes |
|--------|-------|-------|
| Child session creation | ~500ms | Database write + metadata setup |
| Child query submission | ~50ms | REST API call overhead |
| Child execution time | Variable | Depends on profile type (SQL vs LLM-only) |
| Synthesis time | ~2-5s | Coordinator LLM processes all results |
| Total coordination time | 10-30s | For 3 children running in parallel |

**Troubleshooting:**

**Issue: Genie query returns 400 "Not a genie profile"**
- **Cause**: Session's profile is not type `genie`
- **Fix**: Create session with a Genie profile, or use regular query endpoint

**Issue: Child sessions not appearing in `/slaves` endpoint**
- **Cause**: Coordination hasn't started or failed early
- **Fix**: Check task status via `GET /api/v1/tasks/{task_id}` for errors

**Issue: Child results empty or incomplete**
- **Cause**: Child profile misconfigured or MCP tools unavailable
- **Fix**: Test child profile independently, verify MCP server connectivity

**Issue: Synthesis produces generic response**
- **Cause**: Coordinator LLM insufficient for synthesis complexity
- **Fix**: Upgrade to more powerful LLM (e.g., Claude Opus), or customize synthesis prompt

---

### 3.20. Extension Management

Manage post-processing extensions — list, activate, deactivate, scaffold, and edit.

#### List All Extensions

```bash
curl -X GET http://localhost:5050/api/v1/extensions \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "extensions": [
    {
      "extension_id": "json",
      "display_name": "JSON Formatter",
      "description": "Formats LLM answers as structured JSON",
      "extension_tier": "standard",
      "requires_llm": false,
      "output_target": "chat_append",
      "version": "1.0.0",
      "category": "Formatting",
      "is_builtin": true
    }
  ]
}
```

#### List Activated Extensions

```bash
curl -X GET http://localhost:5050/api/v1/extensions/activated \
  -H "Authorization: Bearer $JWT"
```

#### Activate Extension

```bash
curl -X POST http://localhost:5050/api/v1/extensions/json/activate \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"default_param": "minimal"}'
```

#### Deactivate Extension

```bash
curl -X POST http://localhost:5050/api/v1/extensions/activations/json/deactivate \
  -H "Authorization: Bearer $JWT"
```

#### Delete Activation

```bash
curl -X DELETE http://localhost:5050/api/v1/extensions/activations/json2 \
  -H "Authorization: Bearer $JWT"
```

#### Update Activation Config

```bash
curl -X PUT http://localhost:5050/api/v1/extensions/activations/json/config \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"default_param": "full"}'
```

#### Rename Activation

```bash
curl -X PUT http://localhost:5050/api/v1/extensions/activations/json2/rename \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"new_name": "json_full"}'
```

#### Get Extension Source Code

```bash
curl -X GET http://localhost:5050/api/v1/extensions/json/source \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "name": "json",
  "source": "# Python source code...",
  "manifest": { "extension_tier": "standard", ... }
}
```

#### Save Extension Source Code

Saves edited source code for user extensions (under `~/.tda/extensions/`). Built-in extensions return `403`.

```bash
curl -X PUT http://localhost:5050/api/v1/extensions/my_ext/source \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"source": "EXTENSION_NAME = \"my_ext\"\n\ndef transform(answer_text, param=None):\n    return {\"length\": len(answer_text)}\n"}'
```

**Response:**
```json
{
  "status": "success",
  "name": "my_ext",
  "loaded": true
}
```

#### Scaffold Extension (Create from Template)

Creates a new extension skeleton and writes it to `~/.tda/extensions/`.

```bash
curl -X POST http://localhost:5050/api/v1/extensions/scaffold \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"name": "my_ext", "level": "convention", "description": "Custom extension"}'
```

| Level | Files Created |
|-------|--------------|
| `convention` | `~/.tda/extensions/my_ext.py` |
| `simple` | `~/.tda/extensions/my_ext/my_ext.py` |
| `standard` | `~/.tda/extensions/my_ext/my_ext.py` + `manifest.json` |
| `llm` | `~/.tda/extensions/my_ext/my_ext.py` + `manifest.json` |

**Response:**
```json
{
  "status": "success",
  "path": "/Users/you/.tda/extensions/my_ext.py",
  "files": ["my_ext.py"],
  "level": "convention",
  "loaded": true
}
```

#### Preview Scaffold (Without Writing)

Returns generated file contents without writing to disk.

```bash
curl -X POST http://localhost:5050/api/v1/extensions/scaffold/preview \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"name": "my_ext", "level": "simple", "description": "Preview test"}'
```

**Response:**
```json
{
  "status": "success",
  "path": "/Users/you/.tda/extensions/my_ext",
  "files": {
    "my_ext.py": "\"\"\"\\n#my_ext extension — Preview test\\n..."
  },
  "level": "simple"
}
```

#### Reload Extensions

Hot-reload all extensions from disk.

```bash
curl -X POST http://localhost:5050/api/v1/extensions/reload \
  -H "Authorization: Bearer $JWT"
```

#### Export Extension

Export an extension as a downloadable `.extension` ZIP file. Works for both built-in and user-created extensions.

```bash
curl -X POST http://localhost:5050/api/v1/extensions/json/export \
  -H "Authorization: Bearer $JWT" \
  --output json.extension
```

**Response:** Binary ZIP file download containing `manifest.json` + `source.py`.

The manifest includes `export_format_version` and `exported_at` metadata. Internal fields (`_is_user`, `_source_path`, etc.) are stripped.

#### Import Extension

Import an extension from an uploaded `.extension` or `.zip` file.

```bash
curl -X POST http://localhost:5050/api/v1/extensions/import \
  -H "Authorization: Bearer $JWT" \
  -F "file=@my_ext.extension"
```

**Response:**
```json
{
  "status": "success",
  "extension_id": "my_ext",
  "name": "My Extension",
  "message": "Extension 'my_ext' imported successfully."
}
```

**Notes:**
- ZIP must contain `source.py` and optionally `manifest.json`
- Extension ID is sanitized (alphanumeric + underscore only)
- Returns `403` if admin has disabled custom extension creation

#### Duplicate Extension

Duplicate any extension (built-in or user-created) into a new user extension.

```bash
curl -X POST http://localhost:5050/api/v1/extensions/json/duplicate \
  -H "Authorization: Bearer $JWT"
```

**Response (201):**
```json
{
  "status": "success",
  "extension_id": "json_copy",
  "display_name": "JSON Output (Copy)",
  "message": "Extension duplicated as \"json_copy\"."
}
```

**Notes:**
- Generates unique ID: `{id}_copy`, `{id}_copy_2`, etc.
- Rewrites name references in Python source
- Returns `403` if admin has disabled custom extension creation
- Returns `404` if source extension not found

#### Delete Extension

Delete a user-created extension. Built-in extensions cannot be deleted.

```bash
curl -X DELETE http://localhost:5050/api/v1/extensions/my_ext \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "deleted",
  "extension_id": "my_ext"
}
```

**Error responses:**

| Status | Condition | Body |
|--------|-----------|------|
| `403` | Built-in extension | `{"error": "Built-in extensions cannot be deleted"}` |
| `409` | Has active activations | `{"error": "Extension has active activations. Deactivate them first."}` |
| `404` | Extension not found | `{"error": "Extension 'xyz' not found"}` |

**Notes:**
- Only user-created extensions can be deleted
- All activations must be deactivated before deletion
- Inactive activation rows are cleaned up automatically

---

#### Get Extension Targeted Users

**Endpoint:** `GET /v1/marketplace/extensions/{marketplace_id}/targeted-users`

**Purpose:** Return the current targeted-user grant list for a targeted-visibility extension (publisher only).

**Success Response:**
```json
{
  "status": "success",
  "users": [
    { "user_id": "uuid", "username": "alice", "display_name": "Alice Smith" }
  ]
}
```

---

#### Update Extension Targeted Users

**Endpoint:** `PUT /v1/marketplace/extensions/{marketplace_id}/targeted-users`

**Purpose:** Add or remove users from the targeted-visibility grant list (publisher only). Full diff — users in `user_ids` are added if missing; users not in `user_ids` are removed. Visibility must already be `targeted`; changing visibility requires unpublish + republish.

**Request Body:**
```json
{ "user_ids": ["uuid-1", "uuid-2"] }
```

**Success Response:**
```json
{
  "status": "success",
  "users": [
    { "user_id": "uuid-1", "username": "alice", "display_name": "Alice Smith" }
  ]
}
```

**Error Responses:** `401` (unauthenticated), `403` (not publisher), `404` (not found)

> **UI:** Accessible via the **Manage** button on published extension cards. Visibility is locked in manage mode.

---

### 3.21. Skill Management

Skills are pre-processing prompt injections that modify LLM behavior before query execution. The skill system supports discovery, activation, creation, import/export, and a marketplace for sharing skills across users.

**Authentication Required:** All endpoints require JWT authentication.

**Skill Format:** Claude Code compatible — each skill is a `skill.json` manifest + `<name>.md` content file. An optional `uderia` section in the manifest provides platform-specific features (params, injection target).

---

#### 3.21.1. List Available Skills

**Endpoint:** `GET /v1/skills`

**Purpose:** List all available skills (built-in + user-created), filtered by admin governance settings.

**Success Response:**
```json
{
  "skills": [
    {
      "skill_id": "sql-expert",
      "name": "SQL Expert",
      "description": "SQL best practices and optimization guidance",
      "is_builtin": true,
      "injection_target": "system_prompt",
      "tags": ["sql", "database", "best-practices"],
      "allowed_params": ["strict", "lenient"],
      "keywords": ["sql", "query"]
    }
  ],
  "_settings": {
    "user_skills_enabled": true,
    "user_skills_marketplace_enabled": true,
    "skills_mode": "all"
  }
}
```

---

#### 3.21.2. Reload Skills from Disk

**Endpoint:** `POST /v1/skills/reload`

**Purpose:** Hot-reload skills from the filesystem (picks up new/modified skill files).

**Success Response:**
```json
{
  "status": "success",
  "message": "Skills reloaded",
  "count": 8
}
```

---

#### 3.21.3. Get Skill Content

**Endpoint:** `GET /v1/skills/{skill_id}/content`

**Purpose:** Get full skill content and manifest for the editor.

**Success Response:**
```json
{
  "skill_id": "sql-expert",
  "content": "# SQL Expert\n\nYou are an SQL optimization expert...",
  "manifest": {
    "name": "sql-expert",
    "version": "1.0.0",
    "description": "SQL best practices",
    "tags": ["sql", "database"],
    "main_file": "sql-expert.md",
    "uderia": {
      "allowed_params": ["strict", "lenient"],
      "injection_target": "system_prompt"
    }
  }
}
```

---

#### 3.21.3a. Get Skill Library Index

**Endpoint:** `GET /v1/skills/{skill_id}/library-index`

**Purpose:** List the addressable sections of a **progressive-disclosure** skill's bundled
library — the metadata an operator needs to configure an anchor grant, and the same index
the agent chooses from at runtime. Returns **metadata only** (anchor, title, summary);
section bodies are never served here — those are fetched by the agent through the
`TDA_SkillLibrary` component tool, where the per-profile grant is enforced.

Drives the *Library access* dialog on both surfaces (profile editor → Skills; Components →
Skill Library → Libraries).

**Success Response:**
```json
{
  "skill_id": "andp-corpus",
  "progressive": true,
  "count": 77,
  "anchors": [
    {"anchor": "master-design", "title": "Master Design",
     "summary": "The eight-module reference model"},
    {"anchor": "teradata/PLATFORM_PROFILE", "title": "Platform Profile",
     "summary": "Teradata type and storage bindings"}
  ]
}
```

`title` falls back to the anchor and `summary` to `""` when the index omits them, so every
entry is renderable.

**Other responses:**

| Status | Condition | Body |
|---|---|---|
| `200` | Skill exists but does not use progressive disclosure | `{"skill_id", "progressive": false, "anchors": [], "count": 0}` |
| `401` | No authenticated user | `{"error": "Authentication required"}` |
| `404` | Unknown `skill_id` | `{"error": "Skill '<id>' not found."}` |

**Related:** the grant itself is stored per profile at
`skillsConfig.skills[].anchors` (exact anchors + prefix globs; **key absent = all
sections**, and globs are stored verbatim so they grant sections added by later updates).
It is written through the ordinary profile update (`PUT /v1/profiles/{id}`), not through
this endpoint. See [SKILL_ARCHITECTURE §4a](../Architecture/SKILL_ARCHITECTURE.md).

---

#### 3.21.3b. Get Skill Versions (content addressing)

**Endpoint:** `GET /v1/skills/{skill_id}/versions`

Every skill has a deterministic **content address** (sha256 over the markdown
body + the sorted `(relpath, sha256)` pairs of its library files — never
`skill.json`, so metadata/provenance re-stamps do not move it). When a save or
pack install overwrites *different* content, the replaced version is snapshotted
into the append-only version archive (`tda_skills/.skill_versions/<id>/<hash12>/`).

```json
{
  "skill_id": "policing-data-product-consumer",
  "current_content_hash": "9f2ab4…(64 hex)",
  "versions": [
    {"short_hash": "9c01d2e4aa10", "content_hash": "9c01d2…", "archived_at": "2026-08-22T14:01:11+00:00"}
  ]
}
```

**Per-profile version binding (pin):** a `skillsConfig.skills[]` entry may carry
`"content_hash": "<full or ≥12-char prefix>"`. At resolution time the live copy
is served when its address matches the pin; otherwise the archived version is
served. An unsatisfiable pin falls back to the live copy **with a logged
warning** — a wrong version served visibly, never a skill silently absent.
Absent `content_hash` (the default) = live copy, byte-identical baseline.

The pin is operable from the **profile editor's skill row**: a "Version ·
latest / pinned <hash>" chip (rendered only when archived versions exist or a
pin is stored) opens a picker — Follow latest · Pin current · each archived
version. "Follow latest" saves **no key**.

**Coherence rule (progressive skills):** the `TDA_SkillLibrary` component
resolves the SAME version the pin selects — a pinned agent's on-demand library
index and section reads come from the pinned snapshot, never the live
directory, so the injected root and the fetchable library can never diverge.

**Skill list additions:** each row of `GET /v1/skills` now also carries
`pack_history` — the **append-only** pack-install event log
(`[{pack_name, installed_at, owner_user_uuid?}, …]`, oldest first, capped at 20).
`pack_name`/`pack_orphaned` remain the current-pointer fields.

**Cross-owner conflict guard (pack imports):** a pack install that would
overwrite a skill holding *different* content installed by **another user's**
pack is refused for that skill only — the import succeeds and the refusal is
surfaced in the response `warnings` array. Identical content, the same owner's
republish, and legacy skills with no recorded owner all install normally (the
version archive still snapshots whatever is replaced).

#### 3.21.4. Create/Update User Skill

**Endpoint:** `PUT /v1/skills/{skill_id}`

**Request Body:**
```json
{
  "content": "# My Skill\n\nSkill instructions here...",
  "manifest": {
    "name": "my-skill",
    "description": "Custom skill description",
    "tags": ["custom"],
    "version": "1.0.0"
  }
}
```

**Success Response:**
```json
{
  "status": "success",
  "skill_id": "my-skill",
  "message": "Skill saved"
}
```

---

#### 3.21.5. Delete User Skill

**Endpoint:** `DELETE /v1/skills/{skill_id}`

**Purpose:** Delete a user-created skill from disk (built-in skills cannot be deleted).

**Success Response:**
```json
{
  "status": "success",
  "message": "Skill deleted"
}
```

---

#### 3.21.6. Export Skill

**Endpoint:** `POST /v1/skills/{skill_id}/export`

**Purpose:** Export a skill as a `.skill` file (ZIP containing `skill.json` + `<name>.md`).

**Response:** Binary `.skill` file download.

**Response Headers:**
```
Content-Type: application/zip
Content-Disposition: attachment; filename="sql-expert.skill"
```

---

#### 3.21.7. Import Skill

**Endpoint:** `POST /v1/skills/import`

**Purpose:** Import a skill from a `.skill` or `.zip` file.

**Request:** `multipart/form-data` with `file` field.

**Success Response:**
```json
{
  "status": "success",
  "skill_id": "imported-skill",
  "message": "Skill imported: imported-skill"
}
```

---

#### 3.21.8. List Activated Skills

**Endpoint:** `GET /v1/skills/activated`

**Purpose:** Get the current user's activated skills (for `#` autocomplete).

**Success Response:**
```json
{
  "skills": [
    {
      "skill_id": "sql-expert",
      "activation_name": "sql-expert",
      "is_active": true,
      "default_param": "strict"
    }
  ]
}
```

---

#### 3.21.9. Activate Skill

**Endpoint:** `POST /v1/skills/{skill_id}/activate`

**Request Body:**
```json
{
  "activation_name": "my-sql"
}
```

**Success Response:**
```json
{
  "status": "success",
  "activation_name": "my-sql"
}
```

---

#### 3.21.10. Deactivate Skill

**Endpoint:** `POST /v1/skills/activations/{name}/deactivate`

**Purpose:** Soft-deactivate a skill (sets `is_active=0`).

---

#### 3.21.11. Publish Skill to Marketplace

**Endpoint:** `POST /v1/skills/{skill_id}/publish`

**Purpose:** Publish a user-created skill to the marketplace for sharing.

**Prerequisites:**
- Skill must be user-created (not built-in)
- Marketplace must be enabled (`user_skills_marketplace_enabled = true`)

**Request Body:**
```json
{
  "visibility": "public",
  "targeted_user_ids": []
}
```

**Parameters:**
- `visibility` (string, optional) — `public` (default) or `targeted`
- `targeted_user_ids` (array, optional) — For targeted visibility, list of user UUIDs

**Success Response:**
```json
{
  "status": "success",
  "marketplace_id": "a1b2c3d4-...",
  "message": "Skill 'my-skill' published to marketplace"
}
```

**Error Responses:**
| Code | Condition | Example |
|------|-----------|---------|
| `400` | Skill is built-in | `{"error": "Only user-created skills can be published"}` |
| `403` | Marketplace disabled | `{"error": "Skill marketplace is not enabled"}` |
| `409` | Already published | `{"error": "Skill already published by you"}` |
| `404` | Skill not found | `{"error": "Skill not found"}` |

---

#### 3.21.12. Browse Marketplace Skills

**Endpoint:** `GET /v1/marketplace/skills`

**Purpose:** Browse published skills in the marketplace with search, sort, filter, and pagination.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | integer | 1 | Page number |
| `per_page` | integer | 20 | Results per page (max 100) |
| `search` | string | — | Search name, description, tags |
| `sort_by` | string | `recent` | Sort: `recent`, `rating`, `installs`, `downloads`, `name` |
| `injection_target` | string | — | Filter: `system_prompt` or `user_context` |

**Success Response:**
```json
{
  "skills": [
    {
      "id": "a1b2c3d4-...",
      "skill_id": "my-skill",
      "name": "My Skill",
      "description": "A useful skill",
      "version": "1.0.0",
      "author": "admin",
      "injection_target": "system_prompt",
      "has_params": false,
      "tags_json": "[\"sql\", \"database\"]",
      "publisher_username": "admin",
      "visibility": "public",
      "average_rating": 4.5,
      "rating_count": 12,
      "install_count": 45,
      "download_count": 0,
      "published_at": "2026-02-22T10:00:00",
      "is_publisher": false
    }
  ],
  "page": 1,
  "per_page": 20,
  "total_count": 1,
  "total_pages": 1
}
```

---

#### 3.21.13. Get Marketplace Skill Detail

**Endpoint:** `GET /v1/marketplace/skills/{marketplace_id}`

**Purpose:** Get detailed information about a marketplace skill including ratings and manifest.

**Success Response:**
```json
{
  "skill": {
    "id": "a1b2c3d4-...",
    "skill_id": "my-skill",
    "name": "My Skill",
    "description": "A useful skill",
    "version": "1.0.0",
    "author": "admin",
    "injection_target": "system_prompt",
    "has_params": false,
    "tags_json": "[\"sql\"]",
    "manifest_json": "{...}",
    "publisher_username": "admin",
    "average_rating": 4.5,
    "rating_count": 12,
    "install_count": 45,
    "download_count": 0,
    "published_at": "2026-02-22T10:00:00",
    "user_rating": 5,
    "user_comment": "Excellent!"
  }
}
```

---

#### 3.21.14. Install Skill from Marketplace

**Endpoint:** `POST /v1/marketplace/skills/{marketplace_id}/install`

**Purpose:** Install a marketplace skill to the local skills directory.

**Behavior:**
1. Reads `skill.json` + `.md` from marketplace storage
2. Saves to the skills state dir `tda_skills/{skill_id}/` via the skill manager
3. Hot-reloads skill manager
4. Increments install count

**Success Response:**
```json
{
  "status": "success",
  "skill_id": "my-skill",
  "message": "Skill 'My Skill' installed from marketplace"
}
```

**Error Responses:**
| Code | Condition | Example |
|------|-----------|---------|
| `403` | Marketplace disabled | `{"error": "Skill marketplace is not enabled"}` |
| `404` | Not found | `{"error": "Marketplace skill not found"}` |

---

#### 3.21.15. Rate Marketplace Skill

**Endpoint:** `POST /v1/marketplace/skills/{marketplace_id}/rate`

**Purpose:** Submit a 1-5 star rating with optional comment for a marketplace skill.

**Request Body:**
```json
{
  "rating": 5,
  "comment": "Excellent skill, very useful!"
}
```

**Parameters:**
- `rating` (integer, required) — 1 to 5
- `comment` (string, optional) — Review text

**Success Response:**
```json
{
  "status": "success",
  "message": "Rating submitted"
}
```

**Error Responses:**
| Code | Condition | Example |
|------|-----------|---------|
| `400` | Invalid rating | `{"error": "Rating must be between 1 and 5"}` |
| `400` | Self-rating | `{"error": "Cannot rate your own skill"}` |
| `403` | Marketplace disabled | `{"error": "Skill marketplace is not enabled"}` |
| `404` | Not found | `{"error": "Marketplace skill not found"}` |

---

#### 3.21.16. Unpublish Skill from Marketplace

**Endpoint:** `DELETE /v1/marketplace/skills/{marketplace_id}`

**Purpose:** Remove a skill from the marketplace (publisher only). Does not affect users who already installed it.

**Success Response:**
```json
{
  "status": "success",
  "message": "Skill unpublished from marketplace"
}
```

**Error Responses:**
| Code | Condition | Example |
|------|-----------|---------|
| `403` | Not the publisher | `{"error": "Only the publisher can unpublish"}` |
| `404` | Not found | `{"error": "Marketplace skill not found"}` |

---

#### 3.21.16a. Get Skill Targeted Users

**Endpoint:** `GET /v1/marketplace/skills/{marketplace_id}/targeted-users`

**Purpose:** Return the current targeted-user grant list for a targeted-visibility skill (publisher only).

**Success Response:**
```json
{
  "status": "success",
  "users": [
    { "user_id": "uuid", "username": "alice", "display_name": "Alice Smith" }
  ]
}
```

**Error Responses:**
| Code | Condition |
|------|-----------|
| `401` | Not authenticated |
| `403` | Not the publisher |
| `404` | Skill not found in marketplace |

---

#### 3.21.16b. Update Skill Targeted Users

**Endpoint:** `PUT /v1/marketplace/skills/{marketplace_id}/targeted-users`

**Purpose:** Add or remove users from the targeted-visibility grant list (publisher only). Performs a full diff — users in `user_ids` are added if missing; users not in `user_ids` are removed. Visibility must already be `targeted`; changing visibility requires unpublish + republish.

**Request Body:**
```json
{ "user_ids": ["uuid-1", "uuid-2"] }
```

**Success Response:**
```json
{
  "status": "success",
  "users": [
    { "user_id": "uuid-1", "username": "alice", "display_name": "Alice Smith" },
    { "user_id": "uuid-2", "username": "bob", "display_name": "Bob Jones" }
  ]
}
```

**Error Responses:**
| Code | Condition |
|------|-----------|
| `401` | Not authenticated |
| `403` | Not the publisher |
| `404` | Skill not found in marketplace |

> **Note:** The UI exposes this via the **Manage** button on a published skill card (My Assets view and Marketplace browse view). The visibility selector is locked in manage mode — to change visibility, unpublish and republish.

---

#### 3.21.17. Admin Skill Settings

**Endpoint:** `GET /v1/admin/skill-settings`

**Purpose:** Get governance settings including marketplace enabled flag.

**Success Response:**
```json
{
  "settings": {
    "skills_mode": "all",
    "disabled_skills": "[]",
    "user_skills_enabled": "true",
    "auto_skills_enabled": "false",
    "user_skills_marketplace_enabled": "true"
  },
  "builtin_skills": [...]
}
```

**Endpoint:** `POST /v1/admin/skill-settings`

**Purpose:** Update governance settings.

**Request Body:**
```json
{
  "user_skills_enabled": true,
  "user_skills_marketplace_enabled": false
}
```

---

### 3.22. Admin Endpoints

Admin-only endpoints for system management and maintenance operations.

**Authentication Required:**
- All endpoints require admin-level authentication (JWT with admin role)
- Non-admin users receive `403 Forbidden`

**Available Endpoints:**
- Clear Prompt Cache (§3.22.1)
- Users, tiers, consumption, platform settings, operations, enterprise SSO/SAML (§3.22.2)

---

#### 3.22.1. Clear Prompt Cache

**Endpoint:** `POST /v1/admin/prompts/clear-cache`

**Purpose:** Invalidate the in-memory PromptLoader cache to force reloading of system prompts from the database. Used after deploying prompt updates via external scripts (e.g., `update_prompt.py`).

**Authentication:** Admin JWT required

**Request:**
```bash
# Get admin JWT token
JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your_password"}' | jq -r '.token')

# Clear cache
curl -X POST http://localhost:5050/api/v1/admin/prompts/clear-cache \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json"
```

**Response (Success):**
```json
{
  "success": true,
  "message": "Prompt cache cleared successfully"
}
```

**Response (Error):**
```json
{
  "success": false,
  "error": "Failed to clear prompt cache: [error details]"
}
```

**Status Codes:**
- `200 OK` - Cache cleared successfully
- `401 Unauthorized` - Missing or invalid JWT token
- `403 Forbidden` - User is not an admin
- `500 Internal Server Error` - Cache clearing failed

**Use Cases:**

1. **After Prompt Updates**: When deploying updated system prompts via `update_prompt.py`, call this endpoint to ensure changes take effect immediately without restarting the application.

2. **Development/Testing**: Clear cache to test prompt changes during development.

3. **Troubleshooting**: Force reload of prompts if incorrect prompts are being served.

**Example Workflow (Prompt Deployment):**

```bash
# 1. Update prompts in database
cd /path/to/uderia/maintenance/licensing
python update_prompt.py --app-root /path/to/uderia --all

# 2. Clear cache (done automatically by update_prompt.py, but can be manual)
JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}' | jq -r '.token')

curl -X POST http://localhost:5050/api/v1/admin/prompts/clear-cache \
  -H "Authorization: Bearer $JWT"

# 3. Verify new prompts are in use
# Submit a query and check logs for "Using prompt: [prompt_name]"
```

**Technical Details:**

The PromptLoader maintains three in-memory caches:
- `_prompt_cache`: Loaded prompts with resolved parameters
- `_parameter_cache`: Global and prompt-specific parameters
- `_override_cache`: User/profile-level prompt overrides

This endpoint calls `PromptLoader.clear_cache()` which empties all three caches, forcing fresh reads from the database on next prompt access.

**Performance Impact:**
- Cache clearing is instant (~1ms)
- Next prompt access will be slower (~50-100ms) as it reads from database
- Subsequent accesses resume normal cached speed (~1ms)

**Security Considerations:**
- Only admin users can clear cache (prevents unauthorized cache manipulation)
- Cache clearing does not modify database (read-only operation)
- No risk of data loss or corruption

**Monitoring:**

Check application logs for confirmation:
```
INFO - PromptLoader cache cleared by admin request
```

---

#### 3.22.2. Admin Endpoint Reference

Every endpoint below is `require_admin` (non-admins get `403`) unless marked. Implementation:
`api/admin_routes.py` and `api/auth_routes.py`. Pane visibility is §3.1.12a, the schema ledger §3.43,
the Files admin surface §3.44.2.

**Users, tiers, consumption**

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/v1/admin/users` | List users (query `search`, `status`, `profile_tier`, `limit`, `offset`) / create one (required `username`, `email`, `password`; optional `display_name`, `full_name`, `profile_tier`, `pre_verified`; `201`) |
| GET/PATCH/DELETE | `/v1/admin/users/<user_id>` | User detail with audit history / update (activate, deactivate, role) / delete |
| PATCH | `/v1/admin/users/<user_id>/tier` | Change the user's tier: body `{"profile_tier": "user" \| "developer" \| "admin"}`; an admin cannot change their own tier |
| POST | `/v1/admin/users/<user_id>/unlock` | Unlock an account locked by failed logins |
| GET | `/v1/auth/admin/users` | Lighter user list used by the consumption UI |
| GET/POST | `/v1/auth/admin/consumption-profiles` | List / create consumption profiles (`name`, `description`, `prompts_per_hour`, `prompts_per_day`, `config_changes_per_hour`, `input_tokens_per_month`, `output_tokens_per_month`, `is_default`, `is_active`; `409` on a duplicate name) |
| PUT/DELETE | `/v1/auth/admin/consumption-profiles/<id>` | Update / delete (refused while users are assigned) |
| PUT | `/v1/auth/admin/users/<user_id>/consumption-profile` | Assign a consumption profile to a user |
| GET/PUT | `/v1/auth/admin/rate-limit-settings` | Rate-limiting configuration (disabled by default) |
| GET | `/v1/admin/features` · `PATCH /v1/admin/features/<name>/tier` · `POST /v1/admin/features/reset` | Feature → required-tier map / change one (`{required_tier}`) / reset all to defaults |
| GET | `/v1/admin/stats` | System statistics |

**Platform settings**

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/v1/admin/expert-settings` | Expert (advanced runtime) settings |
| GET/PUT | `/v1/admin/genie-settings` | Global Coordinate (genie) settings, each with its lock state |
| GET/PUT | `/v1/admin/knowledge-global-settings` | Global knowledge-repository settings, each with its lock state |
| GET/POST | `/v1/admin/knowledge-config` | Knowledge repository configuration |
| GET/PUT | `/v1/admin/rag-offload-settings` | Platform RAG-offload condensation policy (`backend_policy`, `default_max_store_mb`) |
| GET/POST | `/v1/admin/vectorstore-settings` | Vector-store governance (`allowed_backends_user`, `allowed_backends_developer`, ...) |
| GET/POST | `/v1/admin/extension-settings` | Extension governance + the built-in extension list |
| GET/PUT/DELETE | `/v1/admin/component-settings/users/<component_id>` · `/users/<user_uuid>/<component_id>` | Per-user component access: every user's effective access to a component / set an override `{is_enabled, note?}` / remove it (back to the global setting) |
| GET/PUT | `/v1/admin/window-defaults` | Default panel states, theme and welcome-screen behaviour for new sessions |
| GET/PUT/POST | `/v1/admin/config/document-upload[/<provider>[/reset]]` | Per-provider document-upload configuration (`enabled`, `use_native_upload`, `max_file_size_mb`, `supported_formats_override`, `notes`) / reset to defaults |
| GET/POST | `/v1/admin/tts-config` · `POST /v1/admin/tts-config/test` | Text-to-speech mode (`global` · `user` · `disabled`) and global credentials / test credentials without saving |
| POST | `/v1/admin/mcp-classification` | Run LLM classification of the default profile's MCP tools, prompts and resources now |

**Operations**

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/admin/db-health` | Database health: unreadable reads since start (state, first/last seen, count, last error), the last daily backup and the verified backups on disk (`core/db_health.py`) |
| POST | `/v1/admin/db-health/backup` | Take a verified backup now (`VACUUM INTO` + `quick_check` into `backups/db/`, same routine as the daily `platform-db-backup` job) |
| GET | `/v1/admin/scheduler/platform-jobs` | Platform maintenance jobs with last/next run |
| PUT | `/v1/admin/scheduler/platform-jobs/<task_id>` | Enable/disable one job (`{"enabled": 0\|1}`) |
| POST | `/v1/admin/scheduler/platform-jobs/<task_id>/run-now` · `GET .../runs` | Run a job now / its run history |
| POST | `/v1/admin/scheduler/platform-jobs/{enable,disable}` · `/v1/admin/scheduler/profile-jobs/{enable,disable}` | The two global scheduler gates: platform maintenance jobs / user-created profile jobs |
| POST | `/v1/admin/system-restart/notify` · `/cancel` | Broadcast a maintenance banner to every connected user (`{message, countdown_minutes, downtime_minutes}`) / withdraw it |
| POST | `/v1/admin/clear-cache` · `/v1/admin/reset-state` | Clear the application cache / reset application state (clients must reconnect) |
| POST | `/v1/admin/session-stores/cleanup-orphans` | Destroy external session vector stores whose sessions no longer exist (admin; see the note below) |

> **Note — `/v1/admin/session-stores/cleanup-orphans`.** Admin only. A store is an orphan when NO user
> has its session: the handler judges against every user's sessions (the cross-user session index),
> never the caller's alone, and answers `503` without deleting anything when the index cannot be read.
> Response: `{status, orphaned_sessions_cleaned, session_ids}`.

**Enterprise SSO — OIDC and SAML 2.0** (`auth_routes.py`; configurations admin-only, login flow public)

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/v1/auth/sso/providers` · `/v1/auth/sso/all-providers` | Enabled OIDC providers / all enabled OIDC + SAML providers, for the login page | none |
| GET | `/v1/auth/sso/initiate/<config_id>` · `/v1/auth/sso/callback/<config_id>` | OIDC authorization-code flow: redirect to the IdP / validate the `id_token`, JIT-provision, issue the JWT | none |
| POST | `/v1/auth/sso/backchannel-logout` | OpenID Connect Back-Channel Logout 1.0 (`logout_token` verified against the provider's JWKS, iss/aud, event, iat, jti replay) | none |
| GET/POST | `/v1/auth/sso/configurations` | List / create OIDC configurations (group → tier mapping, JIT provisioning) | admin |
| GET/PUT/DELETE | `/v1/auth/sso/configurations/<id>` · `POST .../test` | Read / update / delete / probe the discovery endpoint | admin |
| GET | `/v1/auth/sso/users` · `/users/<user_id>/sync-history` | SSO-provisioned users with groups and tier / their group-sync history | admin |
| POST | `/v1/auth/sso/users/<user_id>/sync` | Re-apply the group → tier mapping from the stored claims | admin |
| GET | `/v1/auth/saml/<config_id>/initiate` · `POST /v1/auth/saml/<config_id>/acs` | SAML flow: AuthnRequest redirect / Assertion Consumer Service (`SAMLResponse`, `RelayState`) | none |
| GET | `/v1/auth/saml/<config_id>/metadata` | SP metadata XML to register at the IdP | none |
| GET/POST | `/v1/auth/saml/configurations` · GET/PUT/DELETE `/<id>` | SAML IdP configurations | admin |

---

### 3.23. Component Architecture

The Uderia platform includes a pluggable **Component Architecture** that provides rich interactive capabilities beyond plain text responses. Components are auto-discovered from `components/builtin/`, user directories (`~/.tda/components/`), and agent packs. Each component has a backend handler, a frontend renderer (served dynamically), and a manifest defining its capabilities.

**Three builtin components:**

| Component | ID | Tool | Description |
|-----------|----|------|-------------|
| Data Visualization | `chart` | `TDA_Charting` | G2Plot-based charts (16 types) |
| Canvas Workspace | `canvas` | `TDA_Canvas` | CodeMirror 6 code editor with execution |
| Knowledge Graph | `knowledge_graph` | `TDA_KnowledgeGraph` | D3.js entity-relationship graph |

**Authentication Required:** All component endpoints require JWT authentication (`Authorization: Bearer $JWT`).

---

#### 3.23.1. Component Management

Manage installed components — list, inspect, reload, and serve renderers.

##### List All Components

```bash
curl -X GET http://localhost:5050/api/v1/components \
  -H "Authorization: Bearer $JWT"

# Optional: resolve each component's default enablement for a profile TYPE. Used by the
# profile editor when creating a NEW profile (which has no id yet, so the profile-scoped
# endpoint below cannot be used). Adds `profile_enabled_default` to every entry.
curl -X GET "http://localhost:5050/api/v1/components?profile_type=genie" \
  -H "Authorization: Bearer $JWT"
```

**Query parameters:**

| Param | Description |
|---|---|
| `profile_type` | `llm_only` \| `rag_focused` \| `tool_enabled` \| `genie`. When supplied, each component gains `profile_enabled_default` — the resolved default for a new profile of that type (global gate → per-user creation default → manifest `enabled_for`). |

**Enablement fields:** `globally_disabled` (admin gate + per-user override),
`user_profile_default_enabled` (this user's creation default), and — with `profile_type` —
`profile_enabled_default`. For an **existing** profile use
`GET /v1/profiles/<id>/components`, which returns the fully-resolved `profile_enabled`.
All of these come from the one resolver (`components/enablement.py`); no caller re-derives
the rule.

**Response:**
```json
{
  "components": [
    {
      "component_id": "chart",
      "name": "Data Visualization",
      "source": "builtin",
      "version": "1.0.0",
      "description": "Interactive chart visualization using G2Plot"
    },
    {
      "component_id": "canvas",
      "name": "Canvas Workspace",
      "source": "builtin",
      "version": "1.0.0"
    },
    {
      "component_id": "knowledge_graph",
      "name": "Knowledge Graph",
      "source": "builtin",
      "version": "1.0.0"
    }
  ],
  "_settings": {
    "user_components_enabled": true,
    "marketplace_enabled": false
  }
}
```

**Notes:**
- Respects governance settings — disabled components are filtered out
- `_settings` reflects the admin-level component toggle state

---

##### Get Component Details

```bash
curl -X GET http://localhost:5050/api/v1/components/knowledge_graph \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "component": {
    "component_id": "knowledge_graph",
    "name": "Knowledge Graph",
    "description": "Entity-relationship knowledge graph with D3.js visualization",
    "version": "1.0.0",
    "source": "builtin",
    "manifest": { },
    "capabilities": ["action"],
    "supported_types": ["database", "table", "column", "foreign_key", "business_concept", "taxonomy", "metric", "domain"]
  }
}
```

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Component found |
| `404` | Component not found |

---

##### Serve Component Renderer

Returns the component's JavaScript renderer file for dynamic frontend loading.

```bash
curl -X GET http://localhost:5050/api/v1/components/chart/renderer \
  -H "Authorization: Bearer $JWT"
```

**Response:** Raw JavaScript (`Content-Type: application/javascript; charset=utf-8`)

**Notes:**
- No-cache headers applied (`Cache-Control: no-cache, no-store, must-revalidate`)
- Used by the frontend `ComponentRendererRegistry` to load renderers at runtime
- Returns `403` if component is disabled by governance settings
- Returns `404` if renderer file not found

---

##### Get Frontend Manifest

Returns the combined manifest for all components, used by the frontend to register renderers at startup.

```bash
curl -X GET http://localhost:5050/api/v1/components/manifest \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "manifest": {
    "chart": {
      "name": "Data Visualization",
      "renderer_path": "/api/v1/components/chart/renderer",
      "version": "1.0.0",
      "capabilities": ["action"]
    },
    "canvas": {
      "name": "Canvas Workspace",
      "renderer_path": "/api/v1/components/canvas/renderer",
      "version": "1.0.0",
      "capabilities": ["action"]
    },
    "knowledge_graph": {
      "name": "Knowledge Graph",
      "renderer_path": "/api/v1/components/knowledge_graph/renderer",
      "version": "1.0.0",
      "capabilities": ["action"]
    }
  }
}
```

---

##### Hot-Reload Components

Re-discover and reinitialize all components from disk without restarting the application.

```bash
curl -X POST http://localhost:5050/api/v1/components/reload \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "success",
  "loaded": 3
}
```

**Notes:**
- Scans `components/builtin/`, `~/.tda/components/`, and agent pack component directories
- Useful after installing new user components or updating manifests

---

#### 3.23.2. Data Visualization (Chart Component)

The Chart component (`TDA_Charting`) renders interactive charts via G2Plot v2.4.31. It has no dedicated REST endpoints — charts are produced when the LLM invokes the `TDA_Charting` tool during query execution. The component renderer is served via the generic `/v1/components/chart/renderer` endpoint (section 3.23.1).

**Supported Chart Types (16):**

| Type | Description |
|------|-------------|
| `bar` | Horizontal bar chart |
| `column` | Vertical column chart |
| `line` | Line chart |
| `area` | Area chart |
| `pie` | Pie / donut chart |
| `scatter` | Scatter plot |
| `histogram` | Histogram |
| `heatmap` | Heat map |
| `boxplot` | Box-and-whisker plot |
| `wordcloud` | Word cloud |
| `waterfall` | Waterfall chart |
| `radar` | Radar / spider chart |
| `rose` | Rose chart |
| `funnel` | Funnel chart |
| `gauge` | Gauge chart |
| `dualaxes` | Dual-axis chart |
| `treemap` | Treemap chart |

**Render Targets:**
- `inline` — compact chart embedded in chat message
- `sub_window` — split panel (resizable, 800x600 default)

**Profile Integration:**
Charts are enabled per-profile via `componentConfig`:
```json
{
  "componentConfig": {
    "chart": {
      "enabled": true,
      "intensity": "medium"
    }
  }
}
```

Intensity levels: `none` (disabled), `medium` (balanced), `heavy` (aggressive charting instructions to LLM).

---

#### 3.23.3. Canvas Workspace

The Canvas component provides an interactive code editor (CodeMirror 6) with inline AI editing and code execution via MCP bridge or native SQL connectors.

##### Inline AI Code Editing

Lightweight LLM endpoint for modifying selected code based on a natural language instruction. Targets sub-2-second latency (no planner/executor overhead).

```bash
curl -X POST http://localhost:5050/api/v1/canvas/inline-ai \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "selected_code": "SELECT * FROM users",
    "instruction": "Add a WHERE clause for active users only",
    "full_content": "SELECT * FROM users;\nSELECT * FROM orders;",
    "language": "sql"
  }'
```

**Response:**
```json
{
  "status": "success",
  "modified_code": "SELECT * FROM users WHERE is_active = 1",
  "input_tokens": 245,
  "output_tokens": 32
}
```

**Notes:**
- Direct LLM call — no planner/executor pipeline
- Automatically strips markdown fences if the LLM wraps output
- Preserves original indentation

---

##### Execute Code

Execute canvas code via MCP bridge (SQL) or native connector.

```bash
curl -X POST http://localhost:5050/api/v1/canvas/execute \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "SELECT COUNT(*) AS total FROM customers",
    "language": "sql",
    "session_id": "session-abc-123"
  }'
```

**With native connector:**
```bash
curl -X POST http://localhost:5050/api/v1/canvas/execute \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "SELECT COUNT(*) AS total FROM customers",
    "language": "sql",
    "connection_id": "my-teradata-conn"
  }'
```

**On a MongoDB connection** the code is a JSON statement — a collection and an aggregation
pipeline, or a find-style `filter` / `projection` / `sort` / `limit` (see `core/mongo_query.py`):
```bash
curl -X POST http://localhost:5050/api/v1/canvas/execute \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "{\"collection\": \"business\", \"pipeline\": [{\"$group\": {\"_id\": \"$city\", \"n\": {\"$sum\": 1}}}]}",
    "language": "mongodb",
    "connection_id": "dbconn_11b0f7a45f1b"
  }'
```
A MongoDB statement has no MCP-bridge fallback: without `connection_id` it is refused.

**Response:**
```json
{
  "status": "success",
  "result": "total\n-----\n1523",
  "row_count": 1,
  "execution_time_ms": 245,
  "columns": ["total"],
  "rows": [[1523]],
  "truncated": false,
  "grid_truncated": false,
  "masked_columns": []
}
```

The five grid fields are present on the **native-connector** path (a direct Database Connection,
Teradata/JDBC); the MCP-bridge path returns the text `result` only. `rows` holds at most 500 rows
(`CANVAS_GRID_MAX_ROWS`; `grid_truncated: true` when the grid cut the result, `truncated` when
the DATABASE bounded it), JSON-safe (a Decimal/date cell is a string, null stays null).
`masked_columns` names the columns a **view** masks as personal data — decided by the SAME rule
the Database Navigator's preview and query card use (`dbcatalog.intelligence.masked_columns`
over the statement's tables), so the canvas console and the navigator mask identically. The
values are NOT masked in `rows`: the owner ran the statement; the canvas masks in the view with
a "show values" toggle.

**Notes:**
- **Dual execution path**: If `connector_id` or `connection_id` is provided, uses native connector; otherwise falls back to MCP bridge
- Returns specific error if MCP is not connected or credentials are missing

---

##### Explain a Statement (EXPLAIN Plan panel)

`POST /api/v1/canvas/explain` — the plan of a statement on the canvas's bound Database Connection,
without the agent. Runs `EXPLAIN <statement>` (the statement is explained, never executed) on a
read-only connection and returns the parsed plan: the same `spec` the agent's `TDA_ExplainPlan`
produces, drawn by the EXPLAIN Plan panel.

```bash
curl -X POST http://localhost:5050/api/v1/canvas/explain \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"connection_id": "dbconn_40c69618b9f4", "code": "SELECT State, COUNT(*) FROM fitness_db.Customers GROUP BY State"}'
```

Response: `{"status": "success", "spec": {...}, "title": "Plan of the canvas statement · 4 steps · …", "summary": "..."}`
— `spec` carries `steps`, `edges`, `sources`, `findings` (worst first), `total_seconds`, `sql`, `origin: "canvas"`.

| Error `code` | HTTP | When |
|---|---|---|
| `no_connection` | 400 / 404 | no connection given, or not yours |
| `not_teradata` | 400 | the connection is not Teradata (the parser reads Teradata plans) |
| `not_read_only` | 400 | the statement is not exactly one SELECT — refused before the database is asked |
| `explain_failed` | 400 | the database refused the EXPLAIN; `message` is its own first line |
| `unparsed` | 422 | the answer is not a plan the panel can read |


##### List Saved Connections

The Canvas connection routes are adapters over the **one Database Connection store** (the same
store as `/v1/database-connections`, the Database Navigator and PDM deploy): a connection created
here is a central record, and its id (`dbconn_…`) is the same id everywhere. Connections saved
before this as legacy Canvas credentials are listed read-through under their full `canvas_conn_…`
id and still execute; editing one promotes it into the store.

```bash
curl -X GET http://localhost:5050/api/v1/canvas/connections \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "success",
  "connections": [
    {
      "connection_id": "dbconn_40c69618b9f4",
      "name": "Production TD",
      "driver": "teradata",
      "credentials": {
        "name": "Production TD",
        "driver": "teradata",
        "host": "td-prod.example.com",
        "port": 1025,
        "user": "dba_user",
        "password": "••••••••"
      },
      "has_password": true,
      "source": "central"
    }
  ]
}
```

**Notes:**
- Passwords are always masked with `••••••••` in responses
- `credentials.user` is the store's `username` (the Canvas form and SQL connector say `user`)
- `source` is `central`, or `canvas_legacy` for a connection not yet promoted

---

##### Get Connection Details

Accepts any id form (a central id, a full `canvas_conn_…` id, or a bare legacy Canvas id).

```bash
curl -X GET http://localhost:5050/api/v1/canvas/connections/dbconn_40c69618b9f4 \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "success",
  "connection_id": "dbconn_40c69618b9f4",
  "credentials": {
    "name": "Production TD",
    "driver": "teradata",
    "host": "td-prod.example.com",
    "port": 1025,
    "user": "dba_user",
    "password": "••••••••"
  },
  "has_password": true
}
```

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Connection found |
| `404` | Connection not found |

---

##### Save Connection

Create or update a connection in the Database Connection store. Secrets are encrypted separately
from the connection's metadata.

```bash
curl -X PUT http://localhost:5050/api/v1/canvas/connections \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "connection_id": "dbconn_40c69618b9f4",
    "credentials": {
      "name": "Production TD",
      "driver": "teradata",
      "host": "td-prod.example.com",
      "port": 1025,
      "user": "dba_user",
      "password": "s3cret"
    }
  }'
```

**Response:**
```json
{
  "status": "success",
  "message": "Connection saved",
  "connection_id": "dbconn_40c69618b9f4"
}
```

**Notes:**
- Omit `connection_id` to create a connection; the store assigns the id (`dbconn_…`)
- A **legacy** id (`canvas_conn_…` or a bare legacy Canvas id) is promoted: a central record is
  created carrying its credentials merged with the change, and its **new** id is returned
- Sending the masked password back (`••••••••`) keeps the stored password
- An unknown central id returns `400` (it is never silently created)
- `driver` must be one of `teradata`, `postgresql`, `mysql`, `sqlite`, `duckdb`, `mongodb`, `jdbc`
  (declared once in `core/database_connections.DRIVERS`). For the file drivers (`sqlite`,
  `duckdb`) `database` is the file path; a DuckDB path that does not exist is refused, never
  created
- `mongodb`: `host` may be a host name or a full `mongodb://` / `mongodb+srv://` URI (the login
  still comes from `username` / `password`); `database` is optional (empty lists every database;
  set, it scopes the connection to that database); `auth_source` names the database holding the
  login (default `admin`); `allow_writable_login: true` is the owner's consent to agent queries
  on a login that can write. A connection test reports the login's rights in `message` —
  `Connected · login is read-only`, or `Connected · this login can write — …`

---

##### Delete Connection

Accepts any id form. Deleting a connection that was promoted from a legacy one also removes the
legacy original.

```bash
curl -X DELETE http://localhost:5050/api/v1/canvas/connections/dbconn_40c69618b9f4 \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "success",
  "message": "Connection deleted"
}
```

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Deleted |
| `404` | Connection not found |

---

##### Test Connection

Test SQL connection with provided credentials without storing them.

```bash
curl -X POST http://localhost:5050/api/v1/canvas/connections/test \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "credentials": {
      "driver": "ODBC",
      "host": "td-prod.example.com",
      "port": 1025,
      "username": "dba_user",
      "password": "s3cret"
    }
  }'
```

**Response (success):**
```json
{
  "status": "success",
  "valid": true,
  "message": "Connection successful"
}
```

**Response (failure):**
```json
{
  "status": "error",
  "valid": false,
  "message": "Connection refused: host unreachable"
}
```

---

##### Get Starter Templates

Returns canvas starter templates for the template picker UI.

```bash
curl -X GET http://localhost:5050/api/v1/canvas/templates
```

**Response:**
```json
{
  "status": "success",
  "templates": [
    {
      "id": "sql-basic",
      "name": "Basic SQL Query",
      "description": "Simple SELECT query template",
      "language": "sql",
      "code": "SELECT * FROM my_table\nWHERE condition = 'value'\nLIMIT 100;"
    }
  ]
}
```

**Notes:**
- Templates are loaded from `components/builtin/canvas/templates.json`
- Returns empty `templates` array if the file is not found (graceful degradation)
- This endpoint does not require authentication

---

**Stored canvases** (`/api/v1/canvases`, user-scoped; the title is the agent's identity for upserts):

| Method | Path | Behaviour |
|---|---|---|
| `GET` | `/v1/canvases` | List (no content), most recently updated first |
| `POST` | `/v1/canvases` | Create, or update the canvas of the same title (records a version). `content` required |
| `POST` | `/v1/canvases` with `"mode": "create"` | A NEW canvas only (the panel's *New canvas*): never upserts — **409** `title_taken` when the title is in use; `content` may be empty; `title` 1-200 chars |
| `GET` | `/v1/canvases/<id>` | One canvas with its versions |
| `PUT` | `/v1/canvases/<id>` | Update `content` / `title` / `language` / `sources`. A content change records a version unless `"record_version": false` (the editor's autosave); `"record_version": true` with unchanged content records the stored content as a version when the history lacks it (the save when the user leaves the canvas). A `title` another canvas of the user holds → **409** `title_taken`; an empty title → 400 |
| `DELETE` | `/v1/canvases/<id>` | Delete with its versions |
| `GET` | `/v1/canvases/<id>/versions` | Version history |
| `POST` | `/v1/canvases/<id>/versions/<vid>/restore` | Restore (records a version) |

```bash
# a new empty canvas from the panel
curl -X POST http://localhost:5050/api/v1/canvases -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" -d '{"mode":"create","title":"Revenue checks","content":"","language":"sql"}'
# the editor's autosave (no version), then the save on leaving (one version)
curl -X PUT http://localhost:5050/api/v1/canvases/$ID -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" -d '{"content":"SELECT 1","record_version":false}'
curl -X PUT http://localhost:5050/api/v1/canvases/$ID -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" -d '{"content":"SELECT 1","record_version":true}'
```

Renames elsewhere: `PUT /v1/flows/<id>` and `PUT /v1/dashboards/<id>` take `name` (1-200 characters, else 400); `PATCH /v1/knowledge-graph/rename` answers **404** for a graph the caller does not own.

#### 3.23.4. Knowledge Graph

The Knowledge Graph component manages entity-relationship graphs scoped per profile and per user. It stores data in SQLite with NetworkX for graph algorithms, and renders interactive D3.js force-directed visualizations.

**Profile Scoping:** All endpoints accept an optional `profile_id` query/body parameter. If omitted, defaults to `__default__`. Each profile maintains its own isolated graph.

**Entity Types:** `database`, `table`, `column`, `foreign_key`, `business_concept`, `taxonomy`, `metric`, `domain`

**Relationship Types:** `contains`, `foreign_key`, `is_a`, `has_property`, `measures`, `derives_from`, `depends_on`, `relates_to`

---

##### Add Entity

```bash
curl -X POST http://localhost:5050/api/v1/knowledge-graph/entities \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "customers",
    "entity_type": "table",
    "properties": {
      "description": "Main customer table",
      "data_type": "table",
      "business_meaning": "Stores customer demographics"
    },
    "source": "manual",
    "profile_id": "profile-abc"
  }'
```

**Response:**
```json
{
  "status": "success",
  "entity_id": 42
}
```

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Entity created (or upserted if name+type already exists) |
| `400` | Missing `name` or `entity_type` |

---

##### List / Search Entities

```bash
# List all entities (up to 100)
curl -X GET "http://localhost:5050/api/v1/knowledge-graph/entities?profile_id=profile-abc" \
  -H "Authorization: Bearer $JWT"

# Search by name
curl -X GET "http://localhost:5050/api/v1/knowledge-graph/entities?search=customer&limit=20" \
  -H "Authorization: Bearer $JWT"

# Filter by type
curl -X GET "http://localhost:5050/api/v1/knowledge-graph/entities?type=table" \
  -H "Authorization: Bearer $JWT"
```

**Query Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `search` | — | Substring search on entity names |
| `type` | — | Filter by entity type |
| `limit` | `100` | Maximum results |
| `profile_id` | `__default__` | Profile scope |

**Response:**
```json
{
  "status": "success",
  "entities": [
    {
      "id": 42,
      "name": "customers",
      "entity_type": "table",
      "properties": {
        "description": "Main customer table",
        "data_type": "table"
      },
      "source": "manual",
      "created_at": "2026-02-27T10:30:00Z"
    }
  ],
  "count": 1
}
```

---

##### Update Entity

```bash
curl -X PUT http://localhost:5050/api/v1/knowledge-graph/entities/42 \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "properties": {
      "description": "Updated customer table description",
      "row_count": "1.2M"
    }
  }'
```

**Response:**
```json
{
  "status": "success",
  "updated": true
}
```

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Entity updated |
| `404` | Entity not found |

---

##### Delete Entity

Deletes the entity and **cascade-deletes** all relationships where this entity is a source or target.

```bash
curl -X DELETE http://localhost:5050/api/v1/knowledge-graph/entities/42 \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "success",
  "deleted": true
}
```

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Entity and connected relationships deleted |
| `404` | Entity not found |

---

##### Add Relationship

```bash
curl -X POST http://localhost:5050/api/v1/knowledge-graph/relationships \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "source_entity_id": 42,
    "target_entity_id": 55,
    "relationship_type": "contains",
    "cardinality": "1:N",
    "metadata": {
      "description": "customers table contains customer_id column"
    },
    "profile_id": "profile-abc"
  }'
```

**Response:**
```json
{
  "status": "success",
  "relationship_id": 17
}
```

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Relationship created |
| `400` | Missing `source_entity_id`, `target_entity_id`, or `relationship_type` |

---

##### List Relationships

```bash
# All relationships
curl -X GET "http://localhost:5050/api/v1/knowledge-graph/relationships?profile_id=profile-abc" \
  -H "Authorization: Bearer $JWT"

# Relationships connected to a specific entity
curl -X GET "http://localhost:5050/api/v1/knowledge-graph/relationships?entity_id=42" \
  -H "Authorization: Bearer $JWT"
```

**Query Parameters:**
| Parameter | Description |
|-----------|-------------|
| `entity_id` | Filter to relationships involving this entity (source or target) |
| `profile_id` | Profile scope (default: `__default__`) |

**Response:**
```json
{
  "status": "success",
  "relationships": [
    {
      "id": 17,
      "source_entity_id": 42,
      "target_entity_id": 55,
      "relationship_type": "contains",
      "cardinality": "1:N",
      "metadata": {},
      "created_at": "2026-02-27T10:35:00Z"
    }
  ],
  "count": 1
}
```

---

##### Delete Relationship

```bash
curl -X DELETE http://localhost:5050/api/v1/knowledge-graph/relationships/17 \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "success",
  "deleted": true
}
```

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Relationship deleted |
| `404` | Relationship not found |

---

##### Extract Subgraph

Extracts a subgraph centered on an entity using adaptive, entity-type-aware extraction. The algorithm uses three phases:

1. **Phase 1 — Structural Discovery** (unbounded): Traverses FK chains and discovers joinable tables through shared column names (up to 3 iterative rounds for transitive joins). Handles N-way JOIN chains of any length.
2. **Phase 2 — Column Expansion** (budget-aware): Adds columns for discovered tables, prioritizing query-matched tables, until `max_nodes` budget is reached.
3. **Phase 3 — Semantic Enrichment** (capped at 50): Adds `business_concept`, `metric`, `taxonomy`, and `domain` nodes connected to discovered structural entities.

```bash
# Subgraph around a specific entity (adaptive extraction, max 500 nodes)
curl -X GET "http://localhost:5050/api/v1/knowledge-graph/subgraph?entity_name=customers&max_nodes=500" \
  -H "Authorization: Bearer $JWT"

# Full graph (no entity_name)
curl -X GET "http://localhost:5050/api/v1/knowledge-graph/subgraph?max_nodes=100" \
  -H "Authorization: Bearer $JWT"
```

**Query Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `entity_name` | — | Center entity for subgraph extraction |
| `max_nodes` | `500` | Maximum nodes to return (budget for column + semantic expansion) |
| `profile_id` | `__default__` | Profile scope |

**Response:**
```json
{
  "status": "success",
  "nodes": [
    {
      "id": 42,
      "name": "customers",
      "entity_type": "table",
      "properties": {}
    }
  ],
  "edges": [
    {
      "id": 17,
      "source_entity_id": 42,
      "target_entity_id": 55,
      "relationship_type": "contains"
    }
  ],
  "node_count": 8
}
```

**Scalability characteristics:**
| Schema size | Typical result |
|-------------|---------------|
| 10 tables | All tables + all columns (~100 nodes) |
| 100 tables | ~30-50 relevant tables + top-priority columns (~300 nodes) |
| 1000 tables | ~30-100 relevant tables + columns for top ~50 (~500 nodes) |

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Subgraph extracted |
| `404` | `entity_name` provided but not found |

---

##### Get Graph Statistics

```bash
curl -X GET "http://localhost:5050/api/v1/knowledge-graph/stats?profile_id=profile-abc" \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "success",
  "entity_count": 35,
  "relationship_count": 22,
  "entity_types": {
    "table": 12,
    "column": 18,
    "foreign_key": 5
  },
  "relationship_types": {
    "contains": 18,
    "foreign_key": 4
  },
  "graph_density": 0.037,
  "largest_component_size": 30
}
```

---

##### Test Context Enrichment

Test whether the knowledge graph has relevant context for a given query. This is the same enrichment mechanism used automatically during query execution to inject graph context into the planner prompt.

```bash
curl -X GET "http://localhost:5050/api/v1/knowledge-graph/context?query=Show%20all%20customers&profile_id=profile-abc" \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "success",
  "query": "Show all customers",
  "enrichment": {
    "context_text": "--- KNOWLEDGE GRAPH CONTEXT ---\nTABLE ENTITIES:\n  - customers (Main customer table)\n..."
  },
  "has_context": true
}
```

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Enrichment computed |
| `400` | Missing `query` parameter |

---

##### MCP Schema Discovery (not implemented)

`POST /api/v1/knowledge-graph/discover` — MCP schema discovery was never built. The route
answers `501`:

```json
{
  "status": "error",
  "error": "not_implemented",
  "message": "MCP schema discovery is not implemented. Build a knowledge graph with the KG constructor (Intelligence → Knowledge Graphs) or the TDA_KnowledgeGraph tool."
}
```

To populate a graph automatically, use `POST /api/v1/knowledge-graph/generate` (the KG constructors).

---

##### Bulk Import

Import entities and relationships in a single operation. Supports two modes:

- **Create new KG** (default, omit `target_kg_id`): mints a fresh KG UUID, registers metadata via `set_kg_metadata`, and activates it only if the ownership profile has no active KG yet.
- **Merge into existing** (provide `target_kg_id`): imports into the specified existing KG using upsert semantics — no new KG is created and the active KG is unchanged.

```bash
# Create new KG
curl -X POST http://localhost:5050/api/v1/knowledge-graph/import \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "profile_id": "profile-abc",
    "kg_name": "CRM Schema",
    "kg_description": "Customer tables",
    "kg_database_name": "crm_prod",
    "entities": [
      {"name": "customers", "entity_type": "table", "properties": {"description": "Customer master data"}},
      {"name": "orders",    "entity_type": "table", "properties": {"description": "Order transactions"}}
    ],
    "relationships": [
      {"source_name": "orders", "target_name": "customers",
       "relationship_type": "foreign_key", "cardinality": "N:1"}
    ]
  }'

# Merge into an existing KG
curl -X POST http://localhost:5050/api/v1/knowledge-graph/import \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "profile_id": "profile-abc",
    "target_kg_id": "550e8400-e29b-41d4-a716-446655440000",
    "entities": [ {"name": "products", "entity_type": "table"} ],
    "relationships": []
  }'
```

**Response:**
```json
{
  "status": "success",
  "entities_imported": 2,
  "relationships_imported": 1
}
```

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Import completed |
| `400` | Neither `entities` nor `relationships` provided |

---

##### Clear Graph

Delete all entities and relationships for a profile. This is destructive and cannot be undone.

```bash
curl -X DELETE "http://localhost:5050/api/v1/knowledge-graph/clear?profile_id=profile-abc" \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "success",
  "message": "Knowledge graph cleared"
}
```

---

##### List All User Graphs

List all knowledge graphs for the current user across all profiles, with profile metadata.

```bash
curl -X GET http://localhost:5050/api/v1/knowledge-graph/list \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "success",
  "knowledge_graphs": [
    {
      "profile_id": "profile-abc",
      "profile_name": "CRM Optimizer",
      "profile_tag": "OPTIM",
      "profile_type": "tool_enabled",
      "entity_count": 35,
      "relationship_count": 22,
      "created_at": "2026-02-10T08:00:00Z",
      "last_modified": "2026-02-27T10:35:00Z"
    }
  ]
}
```

**Notes:**
- Orphaned graphs (profile was deleted) show as `"profile_name": "Deleted Profile (abc...)"` with empty tag/type

---

##### Export Graph

Download a knowledge graph as a JSON file.

```bash
curl -X GET "http://localhost:5050/api/v1/knowledge-graph/export?profile_id=profile-abc" \
  -H "Authorization: Bearer $JWT" \
  -o knowledge-graph.json
```

**Response:** File download (`Content-Disposition: attachment`)

```json
{
  "export_version": "2.0",
  "kg_id": "550e8400-e29b-41d4-a716-446655440000",
  "kg_name": "CRM Schema",
  "kg_description": "Customer relationship management tables",
  "kg_database_name": "crm_prod",
  "profile_id": "profile-abc",
  "profile_name": "CRM Optimizer",
  "profile_tag": "OPTIM",
  "exported_at": "2026-04-16T10:40:00Z",
  "stats": {
    "entity_count": 35,
    "relationship_count": 22
  },
  "entities": [ ],
  "relationships": [ ]
}
```

> **v1.0 exports** (produced before April 2026) lack `kg_id`/`kg_name` fields but remain import-compatible — they create a new unnamed KG and activate it if the profile has no active KG.

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Graph exported as JSON file |
| `400` | Missing `profile_id` parameter |

---

##### Generate Knowledge Graph

Auto-generates a knowledge graph from MCP server context using a multi-phase ingestion pipeline. The pipeline extracts structural entities (databases, tables, columns) and semantic entities (business concepts, metrics, taxonomies) via LLM-driven analysis, then runs deterministic post-processing phases.

```bash
curl -X POST http://localhost:5050/api/v1/knowledge-graph/generate \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "profile_id": "profile-abc",
    "llm_config_id": "llm-config-456"
  }'
```

**Body Parameters:**
| Parameter | Required | Description |
|-----------|----------|-------------|
| `profile_id` | Yes | Profile whose MCP server provides schema context |
| `llm_config_id` | No | LLM configuration for semantic analysis (Phase 2). If omitted, uses profile default. |

**Constructor selection (`discovery_mode`).** The body above drives the default
LLM-assisted agent-discovery constructor. Three deterministic constructors are selected by
`discovery_mode` and share this endpoint:

| `discovery_mode` | Source | LLM |
|---|---|---|
| *(omitted)* / `prompt` | MCP schema context | yes (semantic phase) |
| `industry_model` | a Databricks industry data model | none |
| `andp_semantic` | a **deployed AI-Native Data Product's own catalog** | none |
| `declared_model` | a model's **ISO/IEC 11179 metadata registry** stored with it on Teradata | none |

###### `discovery_mode: "industry_model"` — deploy options for the demo data

With `deploy_pdm: true` the constructor also realizes the physical model on the chosen
`deployment_target_id` (a Database Connection) and, with `load_demo_data: true`, loads
generated rows — the source ships none. Data options:

| Field | Default | Meaning |
|---|---|---|
| `data_profile` | `"semantic"` | `semantic` — values read from each column's declared `value_regex` / shape and its name (real names, coherent places, log-normal amounts, dates that trend and respect parents, Zipf-skewed foreign keys), volume scaled by table role. `basic` — uniform values of the right type only. |
| `rows_per_table` | `20` | the base volume; `semantic` scales it by role (event ×5, hub ×2, master ×1, reference ×0.4 capped at 40) |
| `max_rows_per_model` | `200000` | upper bound per model for `semantic` (the plan is shrunk proportionally to fit) |

The response's `deploy.data_profile` reports `{profile, base_rows, tables_by_role}`; `deploy.load.total_rows`
the rows loaded. Also: `root_perm_gb` (default 10), `comments` (`none`|`table`|`all`, default `none`),
`replace_existing` (default `true`), `deploy_metric_views`, `validate_with_source`.

**Naming.** `model_size` is the source repo's scope token — `mvm` or `ecm` (`core` / `full`
are accepted as aliases). Every name the platform derives from it uses the platform
vocabulary, never the token: the deployed database, the graph, its root node and every
deployed identifier are **`<industry>_core`** for `mvm` and **`<industry>_full`** for `ecm`
(`agent/kg_constructors/model_naming.py`, the one source). The database is OUR name — the
model's Databricks `deployment_catalog` names their Unity Catalog and is never used as the
Teradata database. A metric view Teradata refuses (a compiled Spark expression it cannot
take) is **skipped and named** in `deploy.ddl.skipped_metric_views` — never a rollback of the
tables — and its `ddl_artifact` node is not imported.

###### `POST /v1/knowledge-graph/redeploy-physical` — re-deploy the physical model in place

The physical sibling of `reload-demo-data`: same `kg_id`, a fresh physical layer. The graph
is rebuilt from its descriptor; the database is (re)created under `root_database` (default:
the one it was deployed in); tables, keys, demo data, statistics (and metric views with
`deploy_metric_views: true`) are deployed with `replace_existing`; the physical layer is
re-imported. A *different* `root_database` is a **rename** — Teradata has no `RENAME DATABASE`,
so the model is deployed under the new name and the previous database dropped afterwards,
**refused (and named in `redeploy.previous_root_drop_error`) if it holds any object the
model does not own**; `drop_previous_root: false` keeps it. `kg_name` renames the graph. A
pre-redeploy snapshot keeps the prior graph switchable (Versions).

| Field | Default | Meaning |
|---|---|---|
| `kg_id` | required | the industry-model graph |
| `deployment_target_id` | the recorded one | a Database Connection |
| `root_database` | the deployed one | the database to deploy into; different = rename |
| `deploy_metric_views` | the recorded choice | one Teradata view per metric |
| `drop_previous_root` | `true` | on a rename, drop the old database if everything in it is the model's |
| `kg_name` | unchanged | rename the graph |
| `data_profile` / `rows_per_table` / `max_rows_per_model` / `comments` | the recorded choices | as for `generate` |

Response: `{status, kg_id, redeploy: {root_database, previous_root_database,
previous_root_dropped, previous_root_drop_error?, convert_summary, ddl, load, statistics,
kg_physical, logical}}`. Available in the graph's **Update** dialog (*Physical model*).

###### `POST /v1/knowledge-graph/redeploy-metric-views` — re-land the metric views only

The views-only sibling of `reload-demo-data` (data only) and `redeploy-physical`
(everything). A metric view is **derived** — compiled from the source model's Spark
expression by `dbdeploy/metric_compiler.py` — so when the compiler learns to render an
expression Teradata previously refused, the fix should reach a deployed model in
**seconds**, not through the hours-long table + data rebuild. The model's metric views are
recompiled with the current compiler and created-or-replaced against the existing tables
through Teradata's **`REPLACE VIEW`** (atomic: a definition Teradata still refuses leaves the
existing view standing — never a drop-then-create hole); tables, rows and statistics are
untouched. A view the target still refuses is **skipped and named**. The graph's physical
layer is patched to what exists afterwards: a created view's `ddl_artifact` node (+
`implements` / `derives_from` edges) is upserted, a refused view's node — if an earlier deploy
left one — is removed. A pre-repair snapshot keeps the prior graph switchable.

| Field | Default | Meaning |
|---|---|---|
| `kg_id` | required | the deployed industry-model graph |
| `deployment_target_id` | the recorded one | a Database Connection |
| `missing_only` | `false` | only create the views the target lacks (refused, not silently widened, if the target cannot be listed) |
| `dry_run` | `false` | plan + generated SQL; the only database I/O is listing the target |

Response: `{status, kg_id, metric_views: {root_database, views_in_model, missing_before,
planned, created, refused: [{view, reason}], ghosts_removed, not_compiled, kg_physical,
generated_sql?}}`. The descriptor records `deploy.deploy_metric_views: true` and
`deploy.metric_views: {at, created, refused}`. Per-instance driver:
`maintenance/repair_metric_views.py` (dry-run default; `--apply`, `--missing-only`,
`--republish` to fan the new artifact nodes out to subscribers). Available in the graph's
**Update** dialog (*Metric views* — Check = dry run, Land).

###### `POST /v1/knowledge-graph/authored/deploy` — land the authored layer (PROP-008 A3)

The person's act on the Govern dialog's road — an agent only **drafts** (§13 of PROP-008);
nothing lands through a tool call. Plans the delta between what was drafted on the graph's
`authored` layer (entities, attributes, their soft RI) and what the target **holds** (its
dictionary), lands it one statement per round trip, stamps the physical layer (`table` /
`column` nodes `implements` the authored nodes; the authored node reads `deployed` with its
`deployed_table` / `deployed_column`) and seals a **`model_change` receipt** — a closed field
tuple (landing · each statement's kind, identifier and hash · landed · skipped · refused ·
errors) Ed25519-signed with the deployment's provenance key, stamped on each landed node
as `deploy_receipt` and shipped as an `ontology.model_change` governance span. Idempotent by
construction: an object the dictionary already holds is planned as `exists`, so a re-run is a
no-op and a half-landed delta resumes with only what is missing.

**Where a new table lands is deployment policy, never a field of this request:** the model's
own database when the deploy login **owns** it (`DBC.DatabasesV.OwnerName` or a `CREATE TABLE`
grant), otherwise the platform-owned sibling `UDERIA_EXT_<root>`, provisioned first.

Body: `kg_id` (required) · `deployment_target_id` (required — a Database Connection the caller
owns; the deploy login is that connection's) · `dry_run` (default `false`: the plan, its SQL
and the summary **in words**, no DDL) · `force_drop` (default `false`: a realisation whose
authored node is gone is **refused** and named, never dropped) · `comments`
(`table` default | `all` | `none`) · `database_parent` / `database_perm_bytes` (provisioning
knobs for a sibling database when one must be created; default: the connection's own).

Response `deploy`: `status` (`dry_run` | `noop` | `success` | `partial` | `error`), `landing`
(`{root_db, owned, database, rule}`), `summary` (words), `plan` (`pending` / `existing` /
`refused` changes, each with `kind`, `identifier`, `node`, `sql_hash`; `warnings`; `standard`;
`pi_decisions`), `result` (`landed`, `skipped`, `errors`), `receipt`, `stamped`, `database_created`,
and — **PROP-008 A4** — `design` and `validated` on EVERY call (a dry run included) plus `readback`
after a landing:

- `design` — what the design standard says about THIS plan before anything runs: `standard`
  (its version, `teradata/1.0`), `system` (the target's version), `rules` (every rule of the
  standard with `id`, `rationale`, `source` ∈ `vendor_doc` | `measured` | `operator`, and its
  `status` on this target — `verified` only when the rule's verification statements were
  executed on this version and recorded, else `unverified` with the reason), `tables` (per planned
  table: `conformance` ∈ `conformant` | `partial` | `nonconformant`, `findings`, and `pi` — the
  primary-index decision with its rule and reason).
- `validated` — the authored nodes the call stamped `validated_at` (+ `status: validated` on a
  draft): **a preview is the validation act**, the road's second step; a nonconformant table is
  never validated.
- `readback` (landing only) — the dictionary read back rule by rule for every table the plan
  created: `status` (`conformant` | `partial` | `nonconformant` | `unverified` — nothing read is
  NOT a clean warehouse), `held` / `violated` / `unverified` counts, `rows` (`table`, `rule`,
  `status`, `detail`, `severity`), `issues` (the conformance gate's shape — `code`, `severity`,
  `message`, `count`, `refs`), `foreign_objects` (in a platform sibling: objects the model did
  not build — each a violated row), `tables_unread`, `summary` (words). The primary index's
  distribution is MEASURED (the `pi_skew_detection` pattern); an empty table reads it
  `unverified`. The node carries the same counts as `readback`, every physical node the deploy
  wrote carries `standard`, and the sealed receipt includes `standard`, `pi_decisions` and the
  readback status.

The PDM deploy report (`generate` with `deploy_pdm`, `redeploy-physical`) carries the same
`standard` and `readback`, plus `probes` — per table the measured facts (`distinct`, `null_ratio`,
`max_width`, `non_latin`, `top_share`, the key's uniqueness and most-frequent share, the role)
and the primary-index `decisions`; `probes.provisional` is `true` on synthetic rows (nothing is
applied — the generator's distribution says nothing about the data) and `probes.applied` says
whether a measured decision changed the DDL (a skewed or non-unique declared key is demoted,
named, and the DDL re-emitted without `UNIQUE`).

Refused with **403 `obda_model_off`** while the OBDA-MODEL component is not available to the caller
(Administration → Components; the planes are components, there is no master switch); 404 for a
graph the caller does not own; 400 without a resolvable target.

**PROP-008 C3 — the backend follows the connection.** The deploy backend is chosen from the
Database Connection's driver, never from the request: `teradata` → the Teradata backend and its
`teradata/1.0` standard, `postgresql` → the PostgreSQL backend and its own `postgresql/1.0`
standard (schemas stand in for databases; the declared key becomes a PRIMARY KEY, a keyless table
is a plain heap, foreign keys are enforced with an index on the referencing column, one `ANALYZE`,
the full comment, quoted identifiers). Any other driver is refused **400 `driver_not_deployable`**.
`deploy.plan`, `design`, `readback` and the receipt carry `backend`; `deploy.infrastructure` is
`{graph, this_deploy, primary}`. **One graph, several warehouses:** a landing on the graph's own
physical-layer infrastructure (`primary: true`) writes the physical nodes, `deployed_table` and
`deploy_receipt` as above; a landing on another one is recorded on the authored node only, as
`realisations[<infrastructure>] = {table | column, deployed_at, standard, readback, receipt}` —
never a second physical layer — and a re-run there resumes from it. `force_drop` applies on the
primary infrastructure only. `GET /v1/knowledge-graph/authored` returns `graph_infrastructure`,
and each node row `realisations` and `landed_on` (the infrastructures it has landed on).

###### `POST /v1/knowledge-graph/authored/publish` — publish an extension (PROP-008 A5)

The lifecycle act on tier 1: stamps the extension's unpublished authored nodes
`status: published` + `published_at` + `published_by`, appends a `publish_extension` audit row,
and rebuilds the released view (Model B) so a released-mode agent reads the extension from
its next turn. **A person's act** — an `agent:<profile_id>` actor is refused under every
policy, and the caller must hold an approver tier even when the ontology's own policy
requires no approval (§13). The conformance gate judges base + this extension.

Body: `kg_id` (the caller's own graph), `extension_id` (default: the caller's,
`ext-<user uuid>`), `names` (optional — publish these nodes only),
`override_nonconformant`.

Responses: `200` `{status:"success", allowed:true, published:[names], conformance, counts,
trust}` · `403` `agent_publish_refused` / `not_an_approver` · `409` `act_off` (master off),
`base_mutation`, `blocked` (conformance) or `nothing_to_publish`. A refusal changes nothing.

`GET /v1/knowledge-graph/authored?kg_id=` now also returns `extensions: [{extension_id, label,
mine, published, unpublished, can_publish, trust:{state, drift, attested_hash, live_hash}}]`
— `drift` names the mover (`base moved` · `extension edited`) on a changed extension — and
each node row carries `extension_id`, `published_at`, `published_by`, `dangling_edges`.

###### `POST /v1/trust/ontology-extension/<kg_id>/<extension_id>/sign-off` — sign an extension

The earned signature for kind `ontology_extension`: refused `not_found` · `nothing_published`
· `base_mutation` · `nonconformant` · `unsigned_ingredient` (the base is not signed-and-intact)
with `422`; `403` when OBDA-MODEL · OBDA-ACT is off or the caller is not a signer; ownership is the GRAPH's
owner (a subscriber signs its own extension of a graph it does not publish). The signed
content binds the extension's published content AND the base's attested hash, so a base
re-sign reads the extension `changed · base moved` while the base's seal is untouched.

**Portability.** A pack export and a marketplace publish carry the **published** authored
nodes only (drafts stay home), each stamped with its extension, plus `trust_extensions`
(`{extension_id: claim}`); every import path (pack install, update-in-place, subscribe,
sync, marketplace install/fork) reinstates them AFTER the base claim — a claim whose content
no longer matches is revoked, never left reading signed. `POST
/v1/knowledge-graph/<profile_id>/publish` with `extension_id` publishes ONE
extension as its own listing (browse rows carry `extension_id` + `extends`); installing it
needs the base: `target_kg_id` or the caller's subscribed copy of the base, else `409
base_required`; fork of an extension listing is `409 extension_listing`.

###### `discovery_mode: "declared_model"` — build from an ISO/IEC 11179 registry

Reads the five registry tables a declared model stores WITH itself (`object_class`,
`data_element`, `value_domain`, `permissible_value`, `association`) plus the data dictionary
for the type rule, and walks them into the two-layer topology: entities, attributes with
definitions / PII / declared-vs-deployed type / decoded values, `foreign_key` edges from
`pk_fk_join` associations, `is_a` from `is_a`, and a **`product_bridge` node** (with
`relates_to` edges to each resolved end) from every `type_correspondence` — a class-level
correspondence that is never a join. Views are typed through `HELP COLUMN` when the
dictionary returns no type for them (QVCI disabled), so a zero-copy view layer is
type-correct. Zero tokens; re-syncable. Reference registry: the Policing Core Model
(`agent_packs/policing-data-products`).

```bash
curl -X POST http://localhost:5050/api/v1/knowledge-graph/generate \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{
    "discovery_mode": "declared_model",
    "profile_id": "profile-abc",
    "connection_id": "dbconn_ac9edc0f6881",
    "catalog_database": "Policing_Core_Semantic",
    "core_database": "Policing_Core",
    "model_name": "Policing Core",
    "graph_name": "Policing Core Model (NIBRS + CASE)"
  }'
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `connection_id` | Yes | A Teradata database connection **belonging to the caller** |
| `catalog_database` | No | The database holding the five registry tables (default `Policing_Core_Semantic`). Discover candidates with `GET /v1/knowledge-graph/declared-registries` |
| `core_database` | No | The database the entities deploy into — the graph's anchor and the grounding gate's trusted scope. Omitted ⇒ read from the registry's `deployed_object` column |
| `model_name` | No | Display name of the logical root; every entity is qualified by it (default `Policing Core`) |
| `include_columns` · `include_physical` | No | Default `true` |
| `max_value_codes` | No | Default `60` — cap on decoded values kept per attribute |
| `validate_with_source` | No | Default `false` — as for `andp_semantic` |
| `graph_name` · `description` · `kg_store_config_id` | No | Name, description, graph-store backend |

**Response:** `kg_id`, `model_name`, `catalog_database`, `scope_databases` (`[core_database]`),
`total`, and `stats` (`entities_logical`, `attributes_logical`, `domains`, `foreign_keys`,
`is_a_edges`, `type_correspondences`, `bridges_resolved` / `bridges_unresolved`,
`type_mismatches`, `pii_columns`, `columns_with_value_codes`). A registry with no
`object_class` rows is HTTP 502.

**`GET /v1/knowledge-graph/declared-registries?connection_id=…`** — the picker's probe: every
database on the connection holding **all five** registry tables, each with its
`core_database` (the database most of its entities deploy into), entity count and subject
areas. Deterministic (a dictionary scan), so the UI offers only registries that exist.

###### `discovery_mode: "andp_semantic"` — build from a deployed data product

Reads the product's published Semantic module (and optionally Memory/Observability), so a
conformant product becomes a grounded agent surface with **zero tokens** and no modelling.
Re-syncable: the graph stores a descriptor and `rebuild()` re-walks it.

```bash
curl -X POST http://localhost:5050/api/v1/knowledge-graph/generate \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{
    "discovery_mode": "andp_semantic",
    "profile_id": "profile-abc",
    "connection_id": "dbconn_ac9edc0f6881",
    "products": ["NIBRS_Source", "CASE_Source"],
    "target_kg_id": "d479fc68-..."
  }'
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `connection_id` | Yes | A database connection **belonging to the caller** (never a raw DSN) |
| `products` | Yes* | Data products to build. Several go into **one** graph — an agent binds one graph, so this is a single act rather than a build plus a remembered append. Order-preserving, de-duplicated. |
| `product` | Yes* | Single-product form; still accepted unchanged. Supply `products` **or** `product`. |
| `target_kg_id` | No | Add to an existing graph you own. Omitted ⇒ a new graph, which **deactivates** the owner profile's current graph (kept, not deleted). |
| `include_columns` · `include_physical` · `include_product_knowledge` · `include_non_business_modules` | No | Default `true`. `include_physical` supplies the deployed layer the grounding gate's trusted scope needs. |
| `validate_with_source` | No | Default `false`. Validate the graph against the SOURCE DATA at build time: the stored shape and range of every text-stored date, whether each declared key is unique, blank ratios of coded columns, real row counts — stamped on the logical nodes (`sampled_shape`, `sample_value`, `date_range`, `row_count`, `key_unique`, `key_duplicate_groups`, `blank_ratio`). One exact scan per table plus cheap sampled reads; every probe fails open. Persisted in the source descriptor so a resync re-samples; `options_override.validate_with_source` on `check-update` / `apply-update` extends or reduces it. The same option exists on the database-discovery constructor (through its resolved `run_sql` tool role) and on the industry-model constructor after its PDM deploy has loaded data. |
| `graph_name` · `kg_store_config_id` · `semantic_database` | No | Name, graph-store backend, explicit Semantic module override |

**Response** — per product, because a build of several has several outcomes:

```json
{
  "status": "success",
  "kg_id": "d479fc68-...",
  "products": [
    {"product": "NIBRS_Source", "status": "success", "entities_added": 3111,
     "semantic_database": "NIBRS_Source_Semantic"},
    {"product": "CASE_Source", "status": "success", "entities_added": 70}
  ],
  "products_built": ["NIBRS_Source", "CASE_Source"],
  "bridge_resolution": {"checked": 2, "resolved": 1},
  "total": {"entities_added": 3181, "relationships_added": 4746}
}
```

| `status` | Meaning |
|---|---|
| `success` | every product built |
| `partial` | at least one built and at least one failed — **never reported as success**: a graph missing a product looks identical to a complete one until an agent is asked something it can no longer answer |
| `error` | nothing built (HTTP 502 for a source error on a single-product request) |

`bridge_resolution` reports a second pass run after the last product lands: a declared
cross-product bridge resolves against whatever existed when *its* product was walked, so a
sequence is one-directional. The pass offers every still-dangling bridge the complete entity
set. It only ever upgrades — an ambiguous target stays dangling, because a declared link whose
target does not exist is the finding.

**Ingestion Pipeline Phases:**

| Phase | Name | Type | Description |
|-------|------|------|-------------|
| 1 | Structural extraction | LLM-driven | Extracts databases, tables, columns from MCP tool traces |
| 2 | Semantic enrichment | LLM-driven | Identifies business concepts, metrics, taxonomies via LLM |
| 3 | Gap-fill | Deterministic | Infers missing `database → table` containment relationships |
| **3.5** | **FK inference** | **Deterministic** | **Infers `foreign_key` relationships between tables using three signals: (A) column properties (FK/References indicators), (B) naming conventions (`<table>_id` patterns), (C) shared column names across tables** |
| 4 | Cross-layer linking | Deterministic | Links semantic entities to structural entities (`relates_to`) |

**Response:**
```json
{
  "status": "success",
  "session_id": "kg-session-abc123",
  "structural": {
    "entities_added": 25,
    "relationships_added": 20
  },
  "semantic": {
    "entities_added": 8,
    "relationships_added": 5
  },
  "phase3_relationships": 3,
  "phase3_5_fk_relationships": 12,
  "phase4_relationships": 6,
  "total": {
    "entities_added": 33,
    "relationships_added": 46
  }
}
```

**Phase 3.5 FK Inference Details:**

The FK inference phase creates `table --[foreign_key]--> table` edges with metadata:

```json
{
  "relationship_type": "foreign_key",
  "cardinality": "1:N",
  "metadata": {
    "inferred_via": "naming_convention",
    "join_column": "customer_id"
  },
  "source": "constructor_fk_inferred"
}
```

Three detection signals (any match creates an edge):
- **Signal A — Column properties**: Scans column properties for FK indicators (`ForeignKey=Y`, `References=TableName`, `IsForeignKey=true`)
- **Signal B — Naming conventions**: Matches `<table_name>_id` or `<table_singular>_id` patterns (e.g., `customer_id` → `customers` table)
- **Signal C — Shared column names**: Finds columns appearing in 2+ tables (excluding generic names like `id`, `name`, `status`). PK-aware: if a column is PK in one table and non-PK in another, cardinality is `1:N`; otherwise `N:M`

**Status Codes:**
| Code | Condition |
|------|-----------|
| `200` | Knowledge graph generated successfully |
| `400` | Missing required parameters |
| `500` | LLM or MCP server error during generation |

---

### 3.24. Context Window Management

The Context Window Management API provides CRUD operations for **context window types** (named compositions of context modules with budget allocations) and management of **context modules** (pluggable units that contribute content to the LLM context window).

Context window types are bound to profiles via `contextWindowTypeId`, controlling how the LLM context budget is allocated across modules like system prompt, tool definitions, conversation history, RAG context, and more.

**Authentication Required:** All endpoints require JWT authentication (`Authorization: Bearer $JWT`).

**Architecture Reference:** See [Context Window Architecture — Section 13](../Architecture/CONTEXT_WINDOW_ARCHITECTURE.md) for the full system design.

---

#### 3.24.1. List Context Window Types

**Endpoint:** `GET /v1/context-window-types`

**Purpose:** Retrieve all context window types configured for the authenticated user.

**Response (200 OK):**
```json
{
  "status": "success",
  "context_window_types": [
    {
      "id": "cwt-default-balanced",
      "name": "Balanced",
      "description": "Default balanced allocation for general use",
      "is_default": true,
      "output_reserve_pct": 12,
      "modules": {
        "system_prompt": { "active": true, "target_pct": 10, "min_pct": 5, "max_pct": 15, "priority": 95 },
        "tool_definitions": { "active": true, "target_pct": 22, "min_pct": 0, "max_pct": 40, "priority": 85 },
        "conversation_history": { "active": true, "target_pct": 22, "min_pct": 10, "max_pct": 60, "priority": 80 },
        "rag_context": { "active": true, "target_pct": 15, "min_pct": 0, "max_pct": 30, "priority": 75 },
        "knowledge_context": { "active": true, "target_pct": 10, "min_pct": 0, "max_pct": 25, "priority": 70 },
        "plan_hydration": { "active": true, "target_pct": 8, "min_pct": 0, "max_pct": 15, "priority": 65 },
        "document_context": { "active": true, "target_pct": 5, "min_pct": 0, "max_pct": 15, "priority": 60 },
        "component_instructions": { "active": true, "target_pct": 4, "min_pct": 0, "max_pct": 10, "priority": 55 },
        "workflow_history": { "active": true, "target_pct": 4, "min_pct": 0, "max_pct": 10, "priority": 50 }
      },
      "condensation_order": [
        "workflow_history", "component_instructions", "document_context",
        "plan_hydration", "knowledge_context", "rag_context",
        "conversation_history", "tool_definitions"
      ],
      "dynamic_adjustments": [
        { "condition": "high_confidence_rag", "action": { "reduce": "tool_definitions", "by_pct": 50 } }
      ]
    }
  ]
}
```

**Example:**
```bash
curl -X GET http://localhost:5050/api/v1/context-window-types \
  -H "Authorization: Bearer $JWT"
```

---

#### 3.24.2. Create Context Window Type

**Endpoint:** `POST /v1/context-window-types`

**Purpose:** Create a new context window type.

**Request Body:**
```json
{
  "name": "RAG Heavy",
  "description": "Maximizes RAG and knowledge context",
  "is_default": false,
  "output_reserve_pct": 15,
  "modules": {
    "system_prompt": { "active": true, "target_pct": 8, "min_pct": 5, "max_pct": 15, "priority": 95 },
    "rag_context": { "active": true, "target_pct": 30, "min_pct": 10, "max_pct": 50, "priority": 85 },
    "knowledge_context": { "active": true, "target_pct": 25, "min_pct": 5, "max_pct": 40, "priority": 80 },
    "conversation_history": { "active": true, "target_pct": 20, "min_pct": 10, "max_pct": 40, "priority": 75 },
    "tool_definitions": { "active": false, "target_pct": 0, "min_pct": 0, "max_pct": 0, "priority": 70 }
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Display name (must be unique) |
| `description` | string | No | Description of the type's purpose |
| `is_default` | boolean | No | Whether this is the default type |
| `output_reserve_pct` | number | No | Percentage of context reserved for LLM output (default: 12) |
| `modules` | object | Yes | Per-module configuration (active, target_pct, min_pct, max_pct, priority) |
| `condensation_order` | array | No | Order in which modules are condensed when over budget |
| `dynamic_adjustments` | array | No | Runtime adjustment rules |

**Response (201 Created):**
```json
{
  "status": "success",
  "message": "Context window type created successfully",
  "context_window_type": { "id": "cwt-1709312000000-a1b2c3d4", "name": "RAG Heavy", "..." }
}
```

**Error Responses:**

| Code | Condition |
|------|-----------|
| `400` | Name already in use or ID already exists |
| `400` | Missing required `name` field |
| `500` | Internal server error |

---

#### 3.24.3. Get Context Window Type

**Endpoint:** `GET /v1/context-window-types/{id}`

**Purpose:** Retrieve a single context window type by ID.

**Response (200 OK):**
```json
{
  "status": "success",
  "context_window_type": {
    "id": "cwt-default-balanced",
    "name": "Balanced",
    "description": "Default balanced allocation for general use",
    "is_default": true,
    "output_reserve_pct": 12,
    "modules": { "..." },
    "condensation_order": ["..."],
    "dynamic_adjustments": ["..."]
  }
}
```

**Error Responses:**

| Code | Condition |
|------|-----------|
| `404` | Context window type not found |

---

#### 3.24.4. Update Context Window Type

**Endpoint:** `PUT /v1/context-window-types/{id}`

**Purpose:** Update an existing context window type. Supports partial updates.

**Request Body:**
```json
{
  "description": "Updated description",
  "output_reserve_pct": 15,
  "modules": {
    "rag_context": { "active": true, "target_pct": 25, "min_pct": 10, "max_pct": 40, "priority": 75 }
  }
}
```

**Response (200 OK):**
```json
{
  "status": "success",
  "message": "Context window type updated successfully"
}
```

**Error Responses:**

| Code | Condition |
|------|-----------|
| `400` | Name already in use by another type |
| `404` | Context window type not found |

---

#### 3.24.5. Delete Context Window Type

**Endpoint:** `DELETE /v1/context-window-types/{id}`

**Purpose:** Delete a context window type. Fails if any profiles are assigned to it.

**Response (200 OK):**
```json
{
  "status": "success",
  "message": "Context window type deleted successfully"
}
```

**Error Responses:**

| Code | Condition |
|------|-----------|
| `400` | Type is assigned to one or more profiles (cannot delete) |
| `404` | Context window type not found |

**Note:** Unassign the type from all profiles before deleting. The error response includes the message identifying which profiles are still assigned.

---

#### 3.24.6. List Installed Modules

**Endpoint:** `GET /v1/context-window/modules`

**Purpose:** List all installed context modules from the module registry, including built-in and user-installed modules.

**Response (200 OK):**
```json
{
  "status": "success",
  "modules": [
    {
      "module_id": "system_prompt",
      "display_name": "System Prompt",
      "version": "1.0.0",
      "description": "Base system prompt with provider-specific formatting",
      "category": "system",
      "source": "builtin",
      "capabilities": {
        "condensable": false,
        "purgeable": false,
        "has_cache": false
      },
      "applicability": {
        "profile_types": ["tool_enabled", "llm_only", "rag_focused", "genie"],
        "required": true
      },
      "defaults": {
        "priority": 95,
        "target_pct": 10,
        "min_pct": 5,
        "max_pct": 15
      }
    }
  ]
}
```

---

#### 3.24.7. Purge Module Data

**Endpoint:** `POST /v1/context-window/modules/{module_id}/purge`

**Purpose:** Clear cached or accumulated data for a specific module (e.g., conversation history, workflow history, uploaded documents).

**Request Body:**
```json
{
  "session_id": "optional-session-id"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | No | Specific session to purge (empty string for all sessions) |

**Response (200 OK):**
```json
{
  "status": "success",
  "result": {
    "purged": true,
    "details": "Cleared conversation history for session abc123"
  }
}
```

**Notes:**
- Only modules with `capabilities.purgeable: true` can be purged
- Non-purgeable modules return `{"purged": false, "reason": "Module is not purgeable"}`
- Purgeable modules: `conversation_history`, `rag_context`, `knowledge_context`, `plan_hydration`, `document_context`, `workflow_history`

---

#### 3.24.8. Get Active Context Window for Profile

**Endpoint:** `GET /v1/context-window/active/{profile_id}`

**Purpose:** Get the resolved context window type for a specific profile, including installed module metadata. Useful for debugging which context configuration a profile will use.

**Response (200 OK):**
```json
{
  "status": "success",
  "context_window_type": {
    "id": "cwt-default-balanced",
    "name": "Balanced",
    "description": "Default balanced allocation for general use",
    "modules": { "..." }
  },
  "profile_id": "profile-default-chat",
  "profile_type": "llm_only",
  "installed_modules": [
    {
      "module_id": "system_prompt",
      "display_name": "System Prompt",
      "capabilities": { "condensable": false, "purgeable": false }
    }
  ]
}
```

**Error Responses:**

| Code | Condition |
|------|-----------|
| `404` | Profile not found or no context window type configured |

**Notes:**
- If the profile has no `contextWindowTypeId`, the default context window type is returned
- The `installed_modules` array includes all modules in the registry, not just those active in the type
- Use this endpoint to verify the module composition before submitting queries

---

#### 3.24.9. Profile Binding

Context window types are bound to profiles via the `contextWindowTypeId` field in the profile configuration.

**Set Context Window Type for a Profile:**

Use the existing profile update endpoint (Section 3.10) with the `contextWindowTypeId` field:

```bash
curl -X PUT "http://localhost:5050/api/v1/profiles/$PROFILE_ID" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "contextWindowTypeId": "cwt-default-balanced"
  }'
```

**Note:** If a profile has no `contextWindowTypeId`, the system falls back to the default context window type (the one with `is_default: true`).

---

#### 3.24.10. Session Context Analytics

**Endpoint:** `GET /v1/sessions/{session_id}/context-analytics`

**Purpose:** Get context window utilization analytics for a specific session. Extracts per-turn snapshot data from workflow history for visualization in the admin UI dashboard.

**Response (200 OK):**
```json
{
  "status": "success",
  "session_id": "c8416070-...",
  "session_name": "My Session",
  "turn_count": 5,
  "context_window_type": { "id": "cwt-default-balanced", "name": "Balanced" },
  "turns": [
    {
      "turn_number": 1,
      "profile_type": "tool_enabled",
      "budget": { "model_limit": 1048576, "available": 922746, "used": 3147, "utilization_pct": 0.3 },
      "modules": [
        { "module_id": "system_prompt", "allocated": 92274, "used": 760, "utilization_pct": 0.8, "condensed": false }
      ],
      "condensation_count": 0,
      "dynamic_adjustments": ["first_turn", "no_documents_attached"]
    }
  ],
  "aggregates": {
    "avg_utilization_pct": 12.3,
    "max_utilization_pct": 45.2,
    "total_condensations": 3,
    "module_avg_utilization": { "system_prompt": 0.8, "tool_definitions": 62.1 },
    "module_condensation_count": { "conversation_history": 2 },
    "adjustment_frequency": { "first_turn": 1, "no_documents_attached": 5 }
  }
}
```

**Error Responses:**

| Code | Condition |
|------|-----------|
| `404` | Session not found |

**Notes:**
- Sessions without context window data (legacy sessions or profiles that don't produce snapshots) return `turn_count: 0` with empty `turns` array
- The endpoint reads from workflow history stored in session files — no cross-session scanning

---

#### 3.24.11. Set Session Context Limit Override

**Endpoint:** `POST /v1/sessions/{session_id}/context-limit`

**Purpose:** Set or clear a session-level context limit override. This temporary override applies only to the current session and takes precedence over both the profile-level override and the model default.

**Priority chain:** session override > profile override > model default (lowest wins)

**Request Body:**
```json
{ "context_limit": 65536 }
```

Set to `null` to clear the override and revert to default:
```json
{ "context_limit": null }
```

**Validation:** `context_limit` must be `null` or an integer >= 4096.

**Response (200 OK):**
```json
{ "status": "success" }
```

**Error Responses:**

| Code | Condition |
|------|-----------|
| `400` | `context_limit` is not null and < 4096, or not a number |
| `401` | Missing or invalid JWT token |

---

#### 3.24.12. Get LLM Configuration Context Limit

**Endpoint:** `GET /v1/llm/configurations/{config_id}/context-limit`

**Purpose:** Look up the maximum context window size for a specific LLM configuration. Resolves the model's context limit from litellm's `model_cost` registry.

**Response (200 OK):**
```json
{
  "status": "success",
  "config_id": "llm-config-123",
  "provider": "Google",
  "model": "gemini-2.0-flash",
  "max_context_tokens": 1048576,
  "source": "litellm"
}
```

**Fallback:** If the model is not found in litellm's registry, returns 128,000 with `"source": "fallback"`.

**Error Responses:**

| Code | Condition |
|------|-----------|
| `401` | Missing or invalid JWT token |
| `404` | LLM configuration not found |

### 3.25. Prompt Execution

Execute MCP prompts directly via REST, without requiring a pre-existing session. The platform creates a temporary session, executes the prompt through the full agent execution pipeline (including skills, tool use, and extensions), and returns the result. The temporary session is archived after execution.

Both endpoints support **profile overrides** (execute with a specific LLM/MCP combination), **skills** (pre-processing context injection), and **extensions** (post-processing output transformation). These features coexist naturally — profile override determines the execution context, skills modify the LLM prompt, and extensions post-process the output.

#### 3.25.1. Execute Prompt

Executes an MCP prompt and returns the LLM response with HTML formatting. Best suited for UI consumption or when you need the formatted response.

**Endpoint:** `POST /api/v1/prompts/{prompt_name}/execute`
**Authentication:** Required (JWT or access token)

**URL Parameters:**
- `prompt_name` (string, required): The MCP prompt name (e.g., `base_tableBusinessDesc`)

**Request Body:**
```json
{
  "arguments": {"database_name": "mydb", "table_name": "employees"},
  "profile_id": "profile-1764006444002-z0hdduce9",
  "skills": [{"name": "concise", "param": null}],
  "extensions": [{"name": "json", "param": null}],
  "canvas_context": {"title": "My Canvas", "language": "sql", "content": "...", "modified": false},
  "attachments": [{"file_id": "file-abc123", "filename": "schema.sql"}]
}
```

**Request Parameters:**
- `arguments` (object, optional): Key-value pairs matching the prompt's expected arguments. Required arguments that are missing will return a `400` error.
- `profile_id` (string, optional): Override the default profile for this execution. Determines which LLM and MCP server combination to use. If omitted, uses the user's default profile.
- `skills` (array, optional): Pre-processing skills to inject into the LLM context. Each element: `{"name": "<skill_name>", "param": "<optional_param>"}`. Skills are resolved and injected before LLM execution.
- `extensions` (array, optional): Post-processing extensions to apply to the LLM output. Each element: `{"name": "<extension_name>", "param": "<optional_param>"}`. Extensions run after LLM execution to transform the output.
- `canvas_context` (object, optional): Bidirectional canvas context with fields `title`, `language`, `content`, and `modified`.
- `attachments` (array, optional): File attachments previously uploaded via `POST /api/v1/chat/upload`.

**Success Response:**
```json
{
  "status": "success",
  "prompt_text": "Describe the business context of table employees in database mydb...",
  "response": "<p>The employees table stores...</p>",
  "response_text": "The employees table stores...",
  "input_tokens": 1234,
  "output_tokens": 567,
  "provider": "Google",
  "model": "gemini-2.0-flash",
  "extension_results": {
    "json": {"business_description": "The employees table stores HR data..."}
  }
}
```

The `extension_results` field is only present when extensions were specified and executed successfully. The `response` field contains HTML-formatted output; `response_text` contains clean text.

**Error Responses:**

| Code | Condition | Example |
|------|-----------|---------|
| `400` | Required argument missing | `{"status": "error", "message": "Required argument 'database_name' is missing"}` |
| `401` | Authentication required | `{"status": "error", "message": "Authentication required"}` |
| `404` | Prompt not found on MCP server | `{"status": "error", "message": "Prompt 'unknown_prompt' not found"}` |
| `500` | Execution returned no result | `{"status": "error", "message": "Prompt 'base_tableBusinessDesc' execution returned no result"}` |
| `503` | Profile activation failed | `{"status": "error", "message": "Failed to activate user profile: ..."}` |

**Examples:**

Basic prompt execution:
```bash
curl -X POST http://localhost:5050/api/v1/prompts/base_tableBusinessDesc/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "arguments": {"database_name": "hr_db", "table_name": "employees"}
  }'
```

Prompt execution with profile override, skill, and extension:
```bash
curl -X POST http://localhost:5050/api/v1/prompts/base_tableBusinessDesc/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "arguments": {"database_name": "hr_db", "table_name": "employees"},
    "profile_id": "profile-1764006444002-z0hdduce9",
    "skills": [{"name": "concise", "param": null}],
    "extensions": [{"name": "json", "param": null}, {"name": "extract", "param": null}]
  }'
```

#### 3.25.2. Execute Prompt (Raw)

Executes an MCP prompt and returns raw, structured execution data including the full execution trace. Designed for programmatic consumption (e.g., auto-generating RAG cases, n8n/Flowise integrations) where structured data is needed rather than HTML-formatted responses.

**Endpoint:** `POST /api/v1/prompts/{prompt_name}/execute-raw`
**Authentication:** Required (JWT or access token)

**URL Parameters:**
- `prompt_name` (string, required): The MCP prompt name

**Request Body:**
```json
{
  "arguments": {"database_name": "mydb", "table_name": "employees"},
  "mcp_server_id": "server-abc123",
  "profile_id": "profile-1764006444002-z0hdduce9",
  "skills": [{"name": "concise", "param": null}],
  "extensions": [{"name": "json", "param": null}],
  "canvas_context": {"title": "My Canvas", "language": "sql", "content": "...", "modified": false},
  "attachments": [{"file_id": "file-abc123", "filename": "schema.sql"}]
}
```

**Request Parameters:**
- `arguments` (object, optional): Key-value pairs matching the prompt's expected arguments.
- `mcp_server_id` (string, optional): Override the MCP server to use. Ignored when `profile_id` is also specified (profile override takes precedence).
- `profile_id` (string, optional): Override the default profile for this execution. Takes precedence over `mcp_server_id`. Determines the full execution context (LLM + MCP server).
- `skills` (array, optional): Pre-processing skills. Same format as Section 3.25.1.
- `extensions` (array, optional): Post-processing extensions. Same format as Section 3.25.1.
- `canvas_context` (object, optional): Bidirectional canvas context.
- `attachments` (array, optional): File attachments previously uploaded via `POST /api/v1/chat/upload`.

**Success Response:**
```json
{
  "status": "success",
  "prompt_text": "Describe the business context of table employees...",
  "execution_trace": [
    {
      "step": 1,
      "type": "tool_call",
      "tool_name": "td_get_table_columns",
      "arguments": {"database": "hr_db", "table": "employees"},
      "result": "Column1 INTEGER, Column2 VARCHAR(100)..."
    }
  ],
  "collected_data": {
    "tables": ["employees"],
    "columns": ["id", "name", "department"]
  },
  "final_answer_text": "The employees table stores HR data...",
  "token_usage": {
    "input": 1234,
    "output": 567,
    "total": 1801
  },
  "provider": "Google",
  "model": "gemini-2.0-flash",
  "extension_results": {
    "json": {"business_description": "..."},
    "extract": {"key_entities": ["employees", "departments"]}
  }
}
```

The `execution_trace` array shows each step the agent took (tool calls, intermediate reasoning). The `extension_results` field is only present when extensions were specified.

**Error Responses:**

| Code | Condition |
|------|-----------|
| `400` | Required argument missing, or no default profile configured |
| `401` | Authentication required |
| `404` | Prompt not found on MCP server |
| `500` | Execution returned no result or prompt text extraction failed |
| `503` | Profile activation failed |

**Examples:**

Raw execution for programmatic consumption:
```bash
curl -X POST http://localhost:5050/api/v1/prompts/base_tableBusinessDesc/execute-raw \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "arguments": {"database_name": "hr_db", "table_name": "employees"}
  }'
```

Raw execution with profile override and extensions:
```bash
curl -X POST http://localhost:5050/api/v1/prompts/base_tableBusinessDesc/execute-raw \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "arguments": {"database_name": "hr_db", "table_name": "employees"},
    "profile_id": "profile-1764006444002-z0hdduce9",
    "skills": [{"name": "concise", "param": null}],
    "extensions": [{"name": "json", "param": null}, {"name": "extract", "param": null}]
  }'
```

**Profile Override vs MCP Server Override:**
- When `profile_id` is specified, it takes full precedence — the profile determines both the LLM and MCP server.
- When only `mcp_server_id` is specified (without `profile_id`), it overrides just the MCP server while keeping the default profile's LLM.
- When neither is specified, the user's default profile is used.

---

### 3.26. Vector Store Configuration Management

Manage named vector store backend configurations. Each configuration binds a backend type (ChromaDB, Qdrant Cloud, or Teradata) to its connection credentials and is referenced by RAG and knowledge collections.

**Supported Backends:**
- `chromadb` — Embedded/local, client-side SentenceTransformer embeddings (default)
- `qdrant` — Qdrant Cloud managed vector DB, client-side embeddings, optional hybrid search
- `teradata` — Teradata In-Database Vector Store (EVS), server-side embeddings via AWS Bedrock or Azure

---

#### 3.26.1. List Allowed Backends

Returns the list of vector store backends available to the current user's tier.

**Endpoint:** `GET /api/v1/vectorstore/allowed-backends`
**Authentication:** Required

**Success Response:**
```json
{
  "status": "success",
  "allowed_backends": ["chromadb", "qdrant", "teradata"],
  "user_tier": "admin"
}
```

---

#### 3.26.2. Get Backend Capabilities

Returns the capability set for a given backend type. Used by the UI to show/hide features (e.g., hybrid search toggle for Qdrant).

**Endpoint:** `GET /api/v1/vectorstore/capabilities?backend_type=qdrant`
**Authentication:** Not required

**Query Parameters:**
- `backend_type` (string): `chromadb`, `qdrant`, or `teradata` (default: `chromadb`)

**Success Response:**
```json
{
  "status": "success",
  "backend_type": "qdrant",
  "capabilities": [
    "CREATE_COLLECTION", "DELETE_COLLECTION", "ADD_DOCUMENTS",
    "DELETE_DOCUMENTS", "SIMILARITY_SEARCH", "GET_BY_ID", "COUNT",
    "UPSERT", "GET_BY_METADATA_FILTER", "UPDATE_METADATA",
    "EMBEDDING_PASSTHROUGH", "GET_ALL", "HYBRID_SEARCH"
  ]
}
```

**Capability Reference:**

| Capability | Backends | Description |
|-----------|---------|-------------|
| `HYBRID_SEARCH` | Qdrant only | Dense + sparse combined search |
| `SERVER_SIDE_EMBEDDING` | Teradata only | LLM embeddings computed server-side |
| `SERVER_SIDE_CHUNKING` | Teradata only | Raw file upload; SDK handles chunking |
| `GET_ALL` | ChromaDB, Qdrant, Teradata | Full collection retrieval |

---

#### 3.26.3. List Vector Store Configurations

**Endpoint:** `GET /api/v1/vectorstore/configurations`
**Authentication:** Required

**Success Response:**
```json
{
  "status": "success",
  "configurations": [
    {
      "id": "vs-default-chromadb",
      "name": "Default (ChromaDB)",
      "backend_type": "chromadb",
      "collection_count": 3
    },
    {
      "id": "vs-qdrant-prod",
      "name": "Qdrant Cloud Production",
      "backend_type": "qdrant",
      "collection_count": 1
    }
  ]
}
```

---

#### 3.26.4. Create Vector Store Configuration

**Endpoint:** `POST /api/v1/vectorstore/configurations`
**Authentication:** Required

**Request Body (Qdrant Cloud):**
```json
{
  "id": "vs-qdrant-prod",
  "name": "Qdrant Cloud Production",
  "backend_type": "qdrant",
  "credentials": {
    "url": "https://your-cluster.qdrant.io",
    "api_key": "your-qdrant-api-key"
  },
  "connection_tested": true
}
```

**Request Body (Teradata):**
```json
{
  "id": "vs-teradata-1",
  "name": "Teradata EVS Production",
  "backend_type": "teradata",
  "credentials": {
    "host": "your-teradata-host",
    "username": "your-user",
    "password": "your-password",
    "database": "your_db"
  },
  "connection_tested": true
}
```

> **Note:** For Teradata configurations, `connection_tested: true` is required — run `POST /api/v1/vectorstore/test-connection` first.

**Validation Rules:**
- `id`: Required, must be unique
- `name`: Required, must be unique for user
- `backend_type`: Must be one of `chromadb`, `qdrant`, `teradata`
- Credentials are encrypted with Fernet before storage; never returned in list responses

**Success Response:** `201 Created`
```json
{
  "status": "success",
  "message": "Vector store configuration created successfully",
  "configuration": { "id": "vs-qdrant-prod", "name": "...", "backend_type": "qdrant" }
}
```

---

#### 3.26.5. Get Vector Store Configuration

Returns configuration details including decrypted credentials (for the configuration editor).

**Endpoint:** `GET /api/v1/vectorstore/configurations/{config_id}`
**Authentication:** Required

**Success Response:**
```json
{
  "status": "success",
  "configuration": {
    "id": "vs-qdrant-prod",
    "name": "Qdrant Cloud Production",
    "backend_type": "qdrant",
    "credentials": {
      "url": "https://your-cluster.qdrant.io",
      "api_key": "your-qdrant-api-key"
    }
  }
}
```

---

#### 3.26.6. Update Vector Store Configuration

**Endpoint:** `PUT /api/v1/vectorstore/configurations/{config_id}`
**Authentication:** Required

**Request Body:** Same structure as create; only provided fields are updated. Omit `credentials` to leave them unchanged.

> **Note:** The `backend_type` of the built-in `vs-default-chromadb` configuration cannot be changed.

**Success Response:**
```json
{
  "status": "success",
  "message": "Vector store configuration updated successfully"
}
```

---

#### 3.26.7. Delete Vector Store Configuration

**Endpoint:** `DELETE /api/v1/vectorstore/configurations/{config_id}`
**Authentication:** Required

> **Warning:** Deleting a configuration that is referenced by active collections will cascade-archive those sessions. Use `GET /api/v1/artifacts/vectorstore-config/{config_id}/relationships` to check impact before deleting.

**Success Response:**
```json
{
  "status": "success",
  "message": "Vector store configuration deleted successfully"
}
```

---

#### 3.26.8. Test Connection (Inline)

Test a vector store connection without saving credentials.

**Endpoint:** `POST /api/v1/vectorstore/test-connection`
**Authentication:** Required

**Request Body:**
```json
{
  "backend_type": "qdrant",
  "credentials": {
    "url": "https://your-cluster.qdrant.io",
    "api_key": "your-qdrant-api-key"
  }
}
```

**Success Response:**
```json
{
  "status": "success",
  "message": "Connection successful",
  "backend_type": "qdrant",
  "details": {
    "collections_count": 5,
    "version": "1.7.4"
  }
}
```

---

#### 3.26.9. Test Saved Configuration Connection

Test connectivity for an already-saved configuration.

**Endpoint:** `POST /api/v1/vectorstore/configurations/{config_id}/test`
**Authentication:** Required

**Success Response:**
```json
{
  "status": "success",
  "message": "Connection successful",
  "config_id": "vs-qdrant-prod"
}
```

**Error Response (connection failed):**
```json
{
  "status": "error",
  "message": "Connection failed: Authentication failed",
  "config_id": "vs-qdrant-prod"
}
```

---

### 3.27. Execution Provenance & Audit Trail

The Execution Provenance Chain (EPC) provides cryptographically verifiable audit trails for every AI-generated response. Each turn stores a signed hash chain (Ed25519) that can be verified offline using the exported public key.

**Use Cases:**
- Regulatory compliance: prove what data was used to generate a response
- Tamper detection: verify no turn was silently modified after the fact
- Offline auditing: export provenance bundles for external review

---

#### 3.27.1. Get Session Provenance

Retrieve provenance chains for all turns in a session.

**Endpoint:** `GET /api/v1/sessions/{session_id}/provenance`
**Authentication:** Required

**Success Response:**
```json
{
  "session_id": "session-abc123",
  "turns": [
    {
      "turn": 1,
      "profile_type": "tool_enabled",
      "status": "success",
      "provenance": {
        "provenance_chain": [
          {
            "step": "strategic_plan",
            "hash": "sha256:abc123...",
            "signature": "ed25519:xyz...",
            "timestamp": "2026-04-13T10:00:00Z"
          }
        ],
        "provenance_meta": {
          "session_id": "session-abc123",
          "turn": 1,
          "profile_tag": "OPTIM"
        }
      }
    }
  ],
  "total_turns": 3,
  "turns_with_provenance": 3
}
```

---

#### 3.27.2. Get Single Turn Provenance

**Endpoint:** `GET /api/v1/sessions/{session_id}/provenance/turn/{turn_number}`
**Authentication:** Required

**Success Response:**
```json
{
  "session_id": "session-abc123",
  "turn": 1,
  "provenance": {
    "provenance_chain": [ ... ],
    "provenance_meta": { ... }
  }
}
```

**Error Responses:**
- `404` — Turn not found or session not found
- `200` with `"provenance": null` — Turn exists but has no provenance data (e.g., older sessions)

---

#### 3.27.3. Verify Provenance Integrity

Cryptographically verify the provenance chain for a session (L1 per-turn hash integrity + L3 cross-turn chain continuity).

**Endpoint:** `POST /api/v1/sessions/{session_id}/provenance/verify`
**Authentication:** Required
**Body:** Empty

**Success Response:**
```json
{
  "session_id": "session-abc123",
  "verification": {
    "valid": true,
    "turns_verified": 3,
    "turns_failed": 0,
    "details": [
      { "turn": 1, "valid": true },
      { "turn": 2, "valid": true },
      { "turn": 3, "valid": true }
    ]
  }
}
```

**Tamper Detected Response:**
```json
{
  "session_id": "session-abc123",
  "verification": {
    "valid": false,
    "turns_failed": 1,
    "details": [
      { "turn": 2, "valid": false, "reason": "Hash mismatch at step 'tool_result'" }
    ]
  }
}
```

---

#### 3.27.4. Export Provenance (Offline Audit)

Download provenance data as a self-contained JSON file for offline verification. Includes the public key PEM so the verifier does not need to contact the server.

**Endpoint:** `GET /api/v1/sessions/{session_id}/provenance/export`
**Authentication:** Required

**Response:** `application/json` attachment — `provenance_{session_id}.json`

```json
{
  "export_version": 1,
  "session_id": "session-abc123",
  "public_key_pem": "-----BEGIN PUBLIC KEY-----\n...",
  "key_fingerprint": "SHA256:abc123...",
  "turns": [
    {
      "turn": 1,
      "user_query": "Show me all tables in the database",
      "profile_type": "tool_enabled",
      "status": "success",
      "provenance_chain": [ ... ],
      "provenance_meta": { ... }
    }
  ]
}
```

**cURL:**
```bash
curl -X GET "http://localhost:5050/api/v1/sessions/$SESSION_ID/provenance/export" \
  -H "Authorization: Bearer $TOKEN" \
  -o "provenance_${SESSION_ID}.json"
```

---

#### 3.27.5. Get Provenance Public Key

Download the Ed25519 public key PEM used to sign provenance chains. Use this for offline signature verification.

**Endpoint:** `GET /api/v1/provenance/public-key`
**Authentication:** Required

**Success Response:**
```json
{
  "public_key_pem": "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2Vw...\n-----END PUBLIC KEY-----\n",
  "key_fingerprint": "SHA256:abc123...",
  "algorithm": "Ed25519"
}
```

**Error:** `503` if the provenance key has not been initialized.

---

### 3.28. Knowledge Graph Marketplace

Publish, browse, and install database schema knowledge graphs (entity-relationship models) via the marketplace. KGs are linked to profiles and provide schema-aware context for the Fusion Optimizer.

**Admin Control:** The KG marketplace can be disabled globally via `POST /api/v1/admin/kg-marketplace-settings`.

---

#### 3.28.1. Publish Knowledge Graph

Publish the knowledge graph associated with a profile to the marketplace.

**Endpoint:** `POST /api/v1/knowledge-graph/{profile_id}/publish`
**Authentication:** Required

**Request Body:**
```json
{
  "name": "Retail Analytics Schema",
  "description": "Entity-relationship model for the retail_db database",
  "domain": "retail",
  "version": "1.0.0",
  "author": "Data Team",
  "tags": ["retail", "sql", "ecommerce"],
  "visibility": "public"
}
```

For targeted (private) publishing:
```json
{
  "name": "Internal Finance Schema",
  "visibility": "targeted",
  "user_ids": ["uuid-of-user1", "uuid-of-user2"]
}
```

**Validation:**
- Profile's knowledge graph must contain at least one entity
- `visibility`: `"public"` or `"targeted"` (targeted requires `user_ids`)

**Republish-in-place:** when the same publisher publishes a KG that they
already published before (matched on `(kg_id, publisher_user_id)`), the
existing marketplace listing is **updated in place** rather than rejected
with `409`. The marketplace_id, install_count, download_count,
subscriber_count, and published_at are all preserved so subscribers'
`kg_subscriptions` rows continue to point to the same listing. The
`export_content_hash` is recomputed and any active subscribers are
auto-synced via a background fanout task. The response includes a
`republished: true` flag in this case.

**Success Response:** `201 Created`
```json
{
  "status": "success",
  "marketplace_id": "kg-marketplace-uuid",
  "republished": false,
  "message": "Knowledge graph published successfully"
}
```

---

#### 3.28.2. Browse Marketplace Knowledge Graphs

**Endpoint:** `GET /api/v1/marketplace/knowledge-graphs`
**Authentication:** Required

**Query Parameters:**
- `search` (string): Full-text search over name, description, domain
- `domain` (string): Filter by domain (e.g., `retail`, `finance`, `healthcare`)
- `sort` (string): `newest` (default), `popular`, `rating`
- `page` (integer): Page number (default: 1)
- `per_page` (integer): Results per page (default: 20, max: 100)

**Success Response:**
```json
{
  "status": "success",
  "knowledge_graphs": [
    {
      "id": "kg-marketplace-uuid",
      "name": "Retail Analytics Schema",
      "description": "...",
      "domain": "retail",
      "version": "1.0.0",
      "author": "Data Team",
      "tags": ["retail", "sql"],
      "entity_count": 12,
      "relationship_count": 18,
      "install_count": 45,
      "subscriber_count": 7,
      "export_content_hash": "ab12cd…",
      "average_rating": 4.2,
      "published_at": "2026-04-01T10:00:00Z",
      "is_publisher": false,
      "is_subscribed": true,
      "subscription_id": "sub-uuid",
      "subscription_count": 1,
      "is_sync_available": false
    }
  ],
  "total": 1,
  "page": 1,
  "per_page": 20
}
```

**New fields (subscription support, May 2026):**

| Field | Type | Meaning |
|---|---|---|
| `subscriber_count`     | int    | Number of users currently subscribed to this listing |
| `export_content_hash`  | string | SHA-256 of `kg_export.json` — bumps on every republish |
| `is_subscribed`        | bool   | True if the current user has any active subscription to this listing |
| `subscription_id`      | string | First active subscription id (when one user has multiple subs into different profiles) |
| `subscription_count`   | int    | Number of profiles the current user has subscribed this listing into |
| `is_sync_available`    | bool   | True when the publisher's `export_content_hash` differs from the subscriber's `last_synced_export_hash` — drives the **Sync now** button |

---

#### 3.28.3. Get Knowledge Graph Detail

**Endpoint:** `GET /api/v1/marketplace/knowledge-graphs/{marketplace_id}`
**Authentication:** Required

**Success Response:** Full detail including entities list, relationships, ratings summary, and install history.

---

#### 3.28.4. Install Knowledge Graph

Install a marketplace knowledge graph into a target profile.

**Endpoint:** `POST /api/v1/marketplace/knowledge-graphs/{marketplace_id}/install`
**Authentication:** Required

**Request Body:**
```json
{
  "target_profile_id": "profile-1764006444002-z0hdduce9"
}
```

**Success Response:**
```json
{
  "status": "success",
  "message": "Knowledge graph installed into profile 'OPTIM'"
}
```

---

#### 3.28.5. Fork Knowledge Graph

Alias for install — creates a private copy in the target profile.

**Endpoint:** `POST /api/v1/marketplace/knowledge-graphs/{marketplace_id}/fork`
**Authentication:** Required
**Request Body:** Same as install.

---

#### 3.28.6. Rate Knowledge Graph

**Endpoint:** `POST /api/v1/marketplace/knowledge-graphs/{marketplace_id}/rate`
**Authentication:** Required

**Request Body:**
```json
{
  "rating": 5,
  "comment": "Excellent schema coverage, saved hours of discovery work"
}
```

**Validation:** `rating` must be 1–5. One rating per user per KG (update existing if re-rated).

---

#### 3.28.7. Unpublish Knowledge Graph

Remove a knowledge graph from the marketplace (publisher only).

**Endpoint:** `DELETE /api/v1/marketplace/knowledge-graphs/{marketplace_id}`
**Authentication:** Required (publisher only)

**Success Response:**
```json
{
  "status": "success",
  "message": "Knowledge graph unpublished successfully"
}
```

---

#### 3.28.8. Get Knowledge Graph Targeted Users

Return the current targeted-user grant list for a targeted-visibility knowledge graph (publisher only).

**Endpoint:** `GET /api/v1/marketplace/knowledge-graphs/{marketplace_id}/targeted-users`
**Authentication:** Required (publisher only)

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `marketplace_id` | string | Marketplace listing ID (from `marketplace_knowledge_graphs.id`) |

**Success Response:**
```json
{
  "status": "success",
  "users": [
    {
      "user_id": "uuid-of-grantee",
      "username": "alice"
    }
  ]
}
```

**Error Responses:**
| Status | Meaning |
|--------|---------|
| `401` | Not authenticated |
| `403` | Not the publisher of this listing |
| `404` | Marketplace listing not found |

**Example:**
```bash
JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}' | jq -r '.token')

curl -X GET "http://localhost:5050/api/v1/marketplace/knowledge-graphs/mkg-abc123/targeted-users" \
  -H "Authorization: Bearer $JWT"
```

---

#### 3.28.9. Update Knowledge Graph Targeted Users

Add or remove users from the targeted-visibility grant list for a published knowledge graph (publisher only). This is a full replacement — the provided list becomes the complete grant list.

**Endpoint:** `PUT /api/v1/marketplace/knowledge-graphs/{marketplace_id}/targeted-users`
**Authentication:** Required (publisher only)

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `marketplace_id` | string | Marketplace listing ID |

**Request Body:**
```json
{
  "user_ids": ["uuid-of-grantee-1", "uuid-of-grantee-2"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `user_ids` | array | Yes | Complete list of user UUIDs who should have access. Users omitted from this list are immediately revoked. |

**Behaviour:**
- Users present in `user_ids` but not already granted → access added
- Users already granted but absent from `user_ids` → access revoked
- Users already granted and in `user_ids` → unchanged (idempotent)
- Only valid for listings with `visibility = "targeted"`. Public listings return `400`.

**Success Response:**
```json
{
  "status": "success",
  "added": 2,
  "removed": 1
}
```

**Error Responses:**
| Status | Meaning |
|--------|---------|
| `400` | Knowledge graph is not targeted-visibility |
| `401` | Not authenticated |
| `403` | Not the publisher of this listing |
| `404` | Marketplace listing not found |

**Example:**
```bash
curl -X PUT "http://localhost:5050/api/v1/marketplace/knowledge-graphs/mkg-abc123/targeted-users" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"user_ids": ["uuid-alice", "uuid-bob"]}'
```

---

#### 3.28.10. Subscribe to Knowledge Graph

**Endpoint:** `POST /api/v1/marketplace/knowledge-graphs/{marketplace_id}/subscribe`
**Authentication:** Required
**Feature flag:** `kg_subscriptions_enabled` in `kg_marketplace_settings` (admin opt-in; default off)

Subscribe creates a **live-tracking copy** of the publisher's knowledge graph in
the subscriber's target profile. Unlike Fork (independent copy), a subscription:

- Is **read-only** — the local KG is marked `is_subscribed=1` and edits via
  the GraphStore API raise `KnowledgeGraphReadOnlyError`
- **Auto-syncs on every publisher republish** — the local copy is rebuilt
  from the publisher's new export via a background fanout task
- Can be **manually synced** via `POST .../sync` (no-op fast path when the
  hashes already match)
- Is **bound to a single target profile** — to subscribe across multiple
  profiles, call subscribe once per profile

**Request Body:**
```json
{
  "target_profile_id": "profile-uuid",
  "force": false
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `target_profile_id` | string | Yes | The subscriber's profile that will host the local copy. Must belong to the calling user. |
| `force`             | bool   | No  | Bypass the fork-conflict check. If you've previously forked this KG, subscribe will return `409 fork_conflict` unless this is `true`. |

**Validation:**
- Subscriptions feature must be globally enabled
- Marketplace listing must exist; targeted listings require a sharing grant
- **Self-subscribe is blocked** — publisher cannot subscribe to their own listing (`400`)
- **Idempotency:** existing subscription on `(user, marketplace_id, target_profile)`
  returns `409 already_subscribed` with the existing subscription details
- **Fork conflict:** if a fork of the same source listing exists on the same
  profile, returns `409 fork_conflict` unless `force=true`

**Success Response:**
```json
{
  "status": "ok",
  "message": "Subscribed to 'Retail Analytics Schema'",
  "subscription_id": "sub-uuid",
  "local_kg_id": "kg-uuid",
  "entities_added": 42,
  "relationships_added": 87,
  "installed_as_inactive": false,
  "last_synced_export_hash": "ab12cd…"
}
```

`installed_as_inactive=true` indicates the target profile already has an
active KG — the subscription is created in inactive state and the user can
explicitly activate it later.

**Error Responses:**
| Status | Body `error` | Meaning |
|---|---|---|
| `400` | `Cannot subscribe to your own published knowledge graph` | Publisher tried to self-subscribe |
| `400` | `target_profile_id is required` | Missing/empty target profile |
| `403` | `Knowledge graph subscriptions have been disabled by the administrator.` | Feature flag off |
| `404` | `Knowledge graph not found in marketplace` | Listing missing OR targeted-without-grant |
| `404` | `target_profile_id not found or does not belong to your account` | Target profile invalid |
| `409` | `already_subscribed` | Idempotency — same `(marketplace_id, target_profile)` already subscribed |
| `409` | `fork_conflict` | Existing fork on this profile; pass `force=true` to coexist |

**Example:**
```bash
JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"alice"}' | jq -r '.token')

curl -X POST "http://localhost:5050/api/v1/marketplace/knowledge-graphs/mkg-abc123/subscribe" \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"target_profile_id":"profile-uuid"}'
```

---

#### 3.28.11. Unsubscribe from Knowledge Graph

**Endpoint:** `DELETE /api/v1/marketplace/knowledge-graphs/{marketplace_id}/subscribe`
**Authentication:** Required

Removes the subscription, hard-deletes the subscriber's local copy of the KG
(entities, relationships, kg_metadata row, kg_profile_assignments rows), and
decrements `subscriber_count` on the listing.

**Query Parameters:**

| Parameter | Required | Description |
|---|---|---|
| `subscription_id` | conditional | **Required** when the user has multiple subscriptions of the same listing into different profiles. Optional when there's exactly one. |

**Success Response:**
```json
{
  "status": "ok",
  "message": "Unsubscribed",
  "subscription_id": "sub-uuid",
  "local_kg_id": "kg-uuid"
}
```

**Error Responses:**
| Status | Body `error` | Meaning |
|---|---|---|
| `400` | `Multiple subscriptions exist…` | Disambiguate via `?subscription_id=…` (full list returned in `subscriptions[]`) |
| `400` | `Subscription does not match marketplace_id` | The given `subscription_id` belongs to a different listing |
| `403` | `Knowledge graph subscriptions have been disabled…` | Feature flag off |
| `404` | `Subscription not found` / `No active subscription found…` | No matching row |
| `409` | `managed_by_agent_pack` | The row is a copy made by an **agent-pack** subscription (`source_kind = 'agent_pack'`): it is removed with the pack (`DELETE /v1/marketplace/agent-packs/{pack_listing_id}/subscribe`), never on its own. The body names `pack_listing_id` and `subscription_id`. |

**Example:**
```bash
curl -X DELETE \
  "http://localhost:5050/api/v1/marketplace/knowledge-graphs/mkg-abc123/subscribe?subscription_id=sub-uuid" \
  -H "Authorization: Bearer $JWT"
```

---

#### 3.28.12. Sync Subscribed Knowledge Graph

**Endpoint:** `POST /api/v1/marketplace/knowledge-graphs/{marketplace_id}/sync`
**Authentication:** Required

Subscriber-initiated rebuild of the local copy from the publisher's latest
export. Fast-path no-op when the subscriber's `last_synced_export_hash`
already matches the publisher's `export_content_hash`.

This endpoint is also called automatically by the publisher's republish
flow (background fanout) — manual invocation is for recovery from a failed
auto-sync or to pull updates when the auto-sync task hasn't run yet.

**Query Parameters:**

| Parameter | Required | Description |
|---|---|---|
| `subscription_id` | conditional | Required when the user has multiple subscriptions of this listing |

**Success Responses:**

```json
{
  "status": "synced",
  "subscription_id": "sub-uuid",
  "local_kg_id": "kg-uuid",
  "last_synced_export_hash": "ab12cd…",
  "entities_added": 42,
  "relationships_added": 87
}
```

```json
{
  "status": "up_to_date",
  "subscription_id": "sub-uuid",
  "local_kg_id": "kg-uuid",
  "last_synced_export_hash": "ab12cd…"
}
```

**Behavior:**
- Reads the publisher's current `kg_export.json`, computes its SHA-256
- If hash matches `kg_subscriptions.last_synced_export_hash` → fast-path
  `up_to_date` (no DB writes)
- Otherwise: raw-SQL DELETE+INSERT all entities and relationships keyed
  on the subscriber's `local_kg_id`, then UPDATE
  `kg_metadata.name/description/database_name`, then bump
  `last_synced_at` + `last_synced_export_hash`, clear `last_sync_error`
- **Activation state on `kg_profile_assignments` is preserved** across
  sync — the subscriber's per-profile activation choice survives

**Failure handling:** transactional. On exception during rebuild, the
transaction rolls back and `last_sync_error` is stamped on the
subscription row (visible via `GET /v1/knowledge-graph/subscriptions`).
The endpoint returns `500` with the error message.

**Error Responses:**
| Status | Meaning |
|---|---|
| `400` | Multiple subscriptions; specify `?subscription_id=…` |
| `403` | Feature flag off |
| `404` | No active subscription / export data missing on disk |
| `409` | `managed_by_agent_pack` — the row is a copy made by an **agent-pack** subscription; sync it with the pack (`POST /v1/marketplace/agent-packs/{pack_listing_id}/sync`, named in the body) |
| `500` | Rebuild failed; `last_sync_error` stamped |

---

#### 3.28.13. List My Knowledge-Graph Subscriptions

**Endpoint:** `GET /api/v1/knowledge-graph/subscriptions`
**Authentication:** Required

Lists every active subscription owned by the current user, with sync status
joined from the publisher's listing.

**Success Response:**
```json
{
  "status": "success",
  "subscriptions": [
    {
      "subscription_id":         "sub-uuid",
      "source_kg_id":            "publisher-kg-uuid",
      "source_marketplace_id":   "mkt-uuid",
      "target_profile_id":       "profile-uuid",
      "local_kg_id":             "local-kg-uuid",
      "enabled":                 true,
      "subscribed_at":           "2026-05-09T10:00:00Z",
      "last_synced_at":          "2026-05-09T10:00:00Z",
      "last_synced_export_hash": "ab12cd…",
      "last_sync_error":         null,
      "listing_name":            "Retail Analytics Schema",
      "listing_version":         "1.0.0",
      "publisher_username":      "alice",
      "sync_status":             "up_to_date"
    }
  ]
}
```

**Sync status values:**

| Value | Meaning |
|---|---|
| `up_to_date`     | `last_synced_export_hash` matches publisher's `export_content_hash` |
| `sync_available` | Publisher has republished; subscriber's copy is behind |
| `sync_failed`    | Most recent sync raised an error (see `last_sync_error`) |
| `orphaned`       | The publisher unpublished or the marketplace listing is missing — call DELETE/subscribe to clean up |

---

### 3.29. Scheduled Tasks

The Task Scheduler lets users create and manage recurring agent queries that execute automatically on a cron or interval schedule. All endpoints require authentication; users can only access their own tasks.

**All scheduled task endpoints require:** `Authorization: Bearer <token>`

#### 3.29.1. List Scheduled Tasks

**Endpoint:** `GET /api/v1/scheduled-tasks`

**Query Parameters:**
- `profile_id` (optional) — Filter tasks by profile

**Success Response:**
```json
{
  "tasks": [
    {
      "id": "task-abc123",
      "name": "Daily Sales Report",
      "prompt": "Generate a summary of today's sales figures",
      "schedule": "0 8 * * 1-5",
      "enabled": true,
      "profile_id": "profile-xyz",
      "session_id": null,
      "overlap_policy": "skip",
      "max_tokens_per_run": null,
      "output_channel": "email",
      "output_config": {"email": "user@example.com"},
      "last_run_at": "2026-05-03T08:00:00Z",
      "next_run_at": "2026-05-04T08:00:00Z",
      "created_at": "2026-05-01T12:00:00Z"
    }
  ]
}
```

#### 3.29.2. Create Scheduled Task

**Endpoint:** `POST /api/v1/scheduled-tasks`

**Request Body:**
```json
{
  "name": "Weekly Performance Review",
  "prompt": "Generate a performance report for the past week",
  "schedule": "0 9 * * 1",
  "profile_id": "profile-xyz",
  "session_id": null,
  "overlap_policy": "skip",
  "max_tokens_per_run": 50000,
  "output_channel": "webhook",
  "output_config": {"webhook_url": "https://hooks.example.com/report", "bearer_token": "secret"}
}
```

**Schedule formats:**
- Cron: `"0 9 * * 1-5"` (weekdays at 9 AM)
- Interval shorthand: `"interval:30m"`, `"interval:2h"`, `"interval:1d"`, `"interval:300s"`

**Overlap policies:** `skip` (default), `queue`, `allow`
**Output channels:** `null` (SSE only), `email`, `webhook`

**Scheduled action (PROP-008 §6, only while OBDA-MODEL · OBDA-ACT is on).** Instead of a prompt, a task may carry `"action_spec": {"action": "<action type>", "invocation": {"key": {...}, "values": {...}}, "connection_id": "<optional>"}`; `prompt` is then optional. Such a task runs **deterministically** — no session, no LLM, no tokens — as the task's agent (`profile_id`), and the action's **posture** decides each run (enforce writes · review parks for the owner · observe records). Each run in `GET /v1/scheduled-tasks/{id}/runs` carries `action_record` — the act, including its `signed_receipt` (an Ed25519 envelope over `payload` = `{receipt: content, occurrence: {face, ref, session_id, confirmed_by, at}}`, verifiable offline; `ref` is the run id, so two runs of the same write carry two distinct receipts) and `face: "scheduler"`; a refused act is an `error` run. Refused before it is stored: `400 {"error": "action_not_granted", "message": …}` when the agent is not granted the action, no agent resolves, OBDA-MODEL · OBDA-ACT is off, or the spec is malformed — on create and on update (an update is checked against the task's stored agent).

#### 3.29.3. Get / Update / Delete Scheduled Task

**Endpoints:**
- `GET /api/v1/scheduled-tasks/{task_id}`
- `PUT /api/v1/scheduled-tasks/{task_id}` — update any fields
- `DELETE /api/v1/scheduled-tasks/{task_id}`

#### 3.29.4. Get Task Run History

**Endpoint:** `GET /api/v1/scheduled-tasks/{task_id}/runs`

Returns up to 100 most recent runs with `status`, `started_at`, `completed_at`, `tokens_used`, `error`.

#### 3.29.5. Trigger Task Immediately

**Endpoint:** `POST /api/v1/scheduled-tasks/{task_id}/run-now`

Fires the task immediately, bypassing its schedule — **also when the task is paused** (`enabled = 0`; a manual run is the owner's explicit act). Returns `{"run_id": "..."}`. The run it opens is always closed: a task deleted before it starts records the run `skipped` ("the task no longer exists"), and an overlap skip reuses the same run.

#### 3.29.6. Scheduler Status

**Endpoint:** `GET /api/v1/scheduler/status`

Returns `{"running": true, "globally_enabled": true, "job_count": 5}`.

---

### 3.30. Platform Connectors (OAuth)

Platform Connectors give users the ability to connect their personal SaaS accounts (Google, Microsoft 365, Slack) so the agent can act on their behalf. Admin configures app credentials once; users complete the per-account OAuth consent flow.

**All connector endpoints require:** `Authorization: Bearer <token>`

Available connector IDs: `uderia-google`, `uderia-outlook`, `uderia-teams`, `uderia-sharepoint`, `uderia-slack`

#### 3.30.1. Initiate OAuth Flow

**Endpoint:** `GET /api/v1/connectors/{connector_id}/auth`

Redirects the user to the provider's OAuth consent screen. Called by the frontend; not used directly in REST automation.

#### 3.30.2. OAuth Callback (Internal)

**Endpoint:** `GET /api/v1/connectors/{connector_id}/callback`

Handles the provider callback, stores the encrypted token in `messaging_identities`, and redirects to the UI. Internal — not called directly.

#### 3.30.3. Get Connection Status

**Endpoint:** `GET /api/v1/connectors/{connector_id}/status`

**Success Response:**
```json
{
  "connected": true,
  "account_name": "user@example.com",
  "expires_at": "2026-06-03T12:00:00Z"
}
```

#### 3.30.4. Disconnect Account

**Endpoint:** `DELETE /api/v1/connectors/{connector_id}/connection`

Revokes the stored token and removes the `messaging_identities` row for the calling user.

**Success Response:**
```json
{"status": "disconnected"}
```

---

### 3.31. Dashboard Canvas

The Dashboard Canvas lets users pin any chart, table, or KPI from the conversation to a persistent, multi-tile dashboard. Tiles refresh on demand by re-executing their originating prompt through the same agent pipeline.

**All dashboard endpoints require:** `Authorization: Bearer <token>`

**Architecture doc:** [docs/Architecture/DASHBOARD_ARCHITECTURE.md](../Architecture/DASHBOARD_ARCHITECTURE.md)

---

#### 3.31.1. List Dashboards

**Endpoint:** `GET /api/v1/dashboards`

List all dashboards owned by the authenticated user.

**Query Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `component_id` | string | Optional. When supplied, each dashboard in the response gains a boolean `already_contains` field indicating whether it already has a tile from this component source. Used by the pin-to-dashboard picker to highlight dashboards that already hold the chart. |

**Response:**
```json
[
  {
    "id": "dash-4e16b368eab8",
    "name": "Sales Overview",
    "is_default": true,
    "tile_count": 6,
    "created_at": "2026-05-17T14:22:00Z",
    "updated_at": "2026-05-18T09:10:00Z"
  }
]
```

---

#### 3.31.2. Create Dashboard

**Endpoint:** `POST /api/v1/dashboards`

Create a new dashboard. The first dashboard created is automatically set as the default.

**Request Body:**
```json
{
  "name": "My Dashboard",
  "is_default": false
}
```

**Response:** `201 Created`
```json
{
  "id": "dash-4e16b368eab8",
  "name": "My Dashboard",
  "is_default": true,
  "tile_count": 0,
  "created_at": "2026-05-18T10:00:00Z",
  "updated_at": "2026-05-18T10:00:00Z"
}
```

---

#### 3.31.3. Get Dashboard

**Endpoint:** `GET /api/v1/dashboards/{dashboard_id}`

Get a dashboard and all its tiles.

**Response:**
```json
{
  "id": "dash-4e16b368eab8",
  "name": "Sales Overview",
  "is_default": true,
  "created_at": "2026-05-17T14:22:00Z",
  "tiles": [
    {
      "id": "tile-abc123",
      "dashboard_id": "dash-4e16b368eab8",
      "title": "Monthly Revenue",
      "col": 0,
      "row": 0,
      "col_span": 6,
      "row_span": 4,
      "spec": { "type": "bar", "x": "month", "y": "revenue" },
      "refresh_status": "ok",
      "source_session_id": "session-xyz",
      "source_turn_id": "turn-42",
      "source_profile_id": "profile-abc",
      "source_prompt": "Show monthly revenue for 2026",
      "created_at": "2026-05-18T09:00:00Z",
      "updated_at": "2026-05-18T09:05:00Z"
    }
  ]
}
```

---

#### 3.31.4. Update Dashboard

**Endpoint:** `PUT /api/v1/dashboards/{dashboard_id}`

Update dashboard metadata.

**Request Body:**
```json
{
  "name": "Q2 Dashboard",
  "is_default": true
}
```

**Response:** `200 OK` — updated dashboard object (same shape as Create response, without `tiles`).

---

#### 3.31.5. Delete Dashboard

**Endpoint:** `DELETE /api/v1/dashboards/{dashboard_id}`

Delete a dashboard and all its tiles (cascade).

**Response:** `200 OK`
```json
{"status": "deleted"}
```

---

#### 3.31.6. List Tiles

**Endpoint:** `GET /api/v1/dashboards/{dashboard_id}/tiles`

List all tiles in a dashboard, ordered by row then column.

**Response:** Array of tile objects (same shape as `tiles` in Get Dashboard).

---

#### 3.31.7. Add Tile

**Endpoint:** `POST /api/v1/dashboards/{dashboard_id}/tiles`

Add a new tile to a dashboard. Called by the frontend after the user selects a dashboard in the pin-to-dashboard picker, or directly by the `TDA_Dashboard` LLM tool when `add_tile` is invoked with a `dashboard_id`.

**Request Body:**
```json
{
  "title": "Monthly Revenue",
  "component_id": "chart",
  "spec": { "type": "bar", "x": "month", "y": "revenue" },
  "tile_width": 6,
  "tile_height": 4,
  "source_session_id": "session-xyz",
  "source_turn_id": 42,
  "profile_id": "profile-abc",
  "profile_tag": "IDGGT",
  "source_prompt": "Show monthly revenue for 2026"
}
```

`source_turn_id` is the **integer turn number** that produced the visual. The server stamps `mcp_tool_name`/`mcp_args_json` from that specific turn (`_mcp_stamp_for_turn`) so a later turn's tool never leaks onto this tile. `profile_id` and `profile_tag` must be consistent (both identify the profile that ran the turn, override-aware).

**Response:** `201 Created` — the created tile object.

---

#### 3.31.8. Update Tile

**Endpoint:** `PUT /api/v1/dashboards/{dashboard_id}/tiles/{tile_id}`

Update a tile's title, position, size, or spec. Used by edit mode drag-to-resize.

**Request Body** (all fields optional):
```json
{
  "title": "Revenue YTD",
  "col": 0,
  "row": 2,
  "col_span": 12,
  "row_span": 5,
  "spec": { "type": "line", "x": "month", "y": "revenue" }
}
```

**Response:** `200 OK` — updated tile object.

---

#### 3.31.9. Delete Tile

**Endpoint:** `DELETE /api/v1/dashboards/{dashboard_id}/tiles/{tile_id}`

Remove a single tile from a dashboard.

**Response:** `200 OK`
```json
{"status": "deleted"}
```

---

#### 3.31.10. Refresh Tile

**Endpoint:** `POST /api/v1/dashboards/{dashboard_id}/tiles/{tile_id}/refresh`

Re-execute the tile's `source_prompt` through the full agent pipeline (same profile and MCP tools as the original query), in the tile's `source_session_id` with `profile_id` as override. This is **Tier 2/3** refresh (⟳ icon). For **chart** tiles without an MCP stamp the prompt is **presentation-locked** ("reproduce ONLY the chart — no narrative") so the answer is the chart alone. Refresh is **non-destructive**: if no chart can be extracted, the prior visualization is preserved. Returns a `task_id` for polling.

A degenerate `source_prompt` (empty, or the literal author label `you`/`assistant` from a pre-fix pin) is rejected:

**Error:** `422 Unprocessable Entity` — `{"code": "invalid_source_prompt", "error": "…re-pin the visual, or use ⋮ → Edit query…"}`

**Response:** `202 Accepted`
```json
{
  "task_id": "task-refresh-abc123",
  "tile_id": "tile-abc123",
  "dashboard_id": "dash-4e16b368eab8"
}
```

Poll `GET /api/v1/tasks/{task_id}` for completion. On success, the tile's `spec` is updated automatically and `refresh_status` is set to `"ok"`.

---

#### 3.31.10a. Refresh Tile via MCP Replay

**Endpoint:** `POST /api/v1/dashboards/{dashboard_id}/tiles/{tile_id}/refresh-mcp`

**Tier 1** refresh (⚡ bolt icon), available only when the tile has a stamped `mcp_tool_name` — i.e. its data came from exactly one MCP data tool in its originating turn. Replays that tool **directly with no LLM round-trip** and re-applies the tile's stored chart spec, so values update while the visualization stays identical. The stamp is bound to the originating turn (`source_turn_id`), never the session's latest data tool.

**Response:** `200 OK` — the refreshed tile object with updated `spec` and `last_refreshed`.

---

#### 3.31.11. Bulk Update Layout

**Endpoint:** `PUT /api/v1/dashboards/{dashboard_id}/layout`

Atomically update the grid positions of multiple tiles in one request. Called after the user drags to reorder tiles in edit mode.

**Request Body:**
```json
{
  "tiles": [
    { "id": "tile-abc123", "col": 0, "row": 0, "col_span": 6, "row_span": 4 },
    { "id": "tile-def456", "col": 6, "row": 0, "col_span": 6, "row_span": 4 }
  ]
}
```

**Response:** `200 OK`
```json
{"status": "ok", "updated": 2}
```

---

### 3.32. Flow Execution & Monitoring

A **Flow** is a saved, visual workflow (a directed acyclic graph of nodes — LLM calls, tool calls, decisions, functions). This section documents the endpoints for **executing** a flow and **monitoring** its progress. The authoring endpoints (create / patch / layout / versions) are out of scope here — this is the runtime surface that external integrations (n8n, Airflow, schedulers, custom clients) use to fire a flow and watch it run.

**Execution model.** Executing a flow creates a durable **run** (a `frun-…` record persisted in the database — it survives a server restart, unlike the in-memory query Task Object). A run progresses through node-by-node execution and settles in a terminal state. Two monitoring paths are available: **poll** the run endpoint, or **subscribe** to a per-run SSE stream with event replay.

**Session binding.** Every run is bound to a chat session: the one you pass as `session_id`, or — when you omit it — a **fresh session auto-provisioned per run**, named after the flow (`"<flow name>"`) and created under the flow's effective profile (so the sidebar chip matches the run's profile). Each headless run gets its **own** new session; runs are never folded into a prior session (a client that *wants* runs to share a session does the REST-correct thing and passes its own `session_id`). The bound session is returned by `execute`, persisted on the run, and broadcast live (exactly one `new_session_created`) so it appears in the sidebar without a refresh.

**Node session sharing is governed by the flow's execution mode** (`settings.parallelExecution`), not by the request — this guarantees the unsafe "shared session + concurrent writers" combination can never be selected:
- **Serialize** (`parallelExecution: false`, default) → **compact**: all LLM nodes share the bound session and run sequentially; their token usage rolls into that session's totals.
- **Parallelize** (`parallelExecution: true`) → **individual**: each LLM node runs in its own isolated session (so its tokens land in *that* child session, not the bound one) and levels run concurrently.

Either way the run's own `tokens_*` / `cost_usd` (and the cohesive run turn's `flow_total_cost`) are the authoritative per-run totals, correct in both modes.

**Run provenance in the chat UI.** A REST-triggered run records a cohesive "flow-run turn" into the bound session, tagged with both `source='rest'` (the **Rest Call** badge) and `origin='flow'` (the **Flow** badge), so it is visually distinguishable from a conversation-triggered flow (which produces the agent's own turn plus per-node turns, with no Rest Call badge). Conversation-triggered runs do not write this cohesive turn.

**All flow endpoints require:** `Authorization: Bearer <token>` — both JWT and long-lived **access tokens** are accepted (same `@require_auth` path as the query/task endpoints). Browser `EventSource` clients that cannot set headers may instead pass the token on the SSE stream as `?token=<jwt>`.

**Architecture doc:** [docs/Architecture/FLOW_COMPONENT_ARCHITECTURE.md](../Architecture/FLOW_COMPONENT_ARCHITECTURE.md)

#### Run Status vs. the Task Object

Flow runs and query Tasks (§3.6 / §4.1) share the same **terminal** vocabulary, so a Task-aware client recognizes a finished run immediately. The in-flight words differ — a run is never `processing` (it goes `queued` → `running`), and it has no `cancelling` interim state.

| Concept | Query Task (§4.1) | Flow Run |
|---|---|---|
| Identifier | `task_id` (`task-<uuid>`) | `run_id` (`frun-<hex12>`) |
| Storage | in-memory, per-process (lost on restart) | persistent (SQLite) |
| In-flight statuses | `pending`, `processing`, `cancelling` | `queued`, `running` |
| Terminal statuses | `complete`, `error`, `cancelled` | `complete`, `error`, `cancelled` |
| Live stream | per-user channel `/api/notifications/subscribe` (no replay) | per-run stream `/api/v1/flows/runs/{run_id}/stream` (with replay buffer) |
| Per-step detail | flat event log + `intermediate_data` | per-node records (`node_runs` table) |

---

#### 3.32.0. Flow Definitions (CRUD, versions, templates)

Authoring endpoints behind the Flow designer (`api/flow_routes.py`). All require auth and act on the
caller's own flows; platform flows (templates, `is_template=1`) are shared read-only.

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/v1/flows` | List the caller's flows (`?include_templates=`) / create one (`name`, `description?`, `definition?`, or `template` + `params` + `fills` to start from a topology template; `201`) |
| GET/PUT/DELETE | `/v1/flows/<flow_id>` | Read / update metadata or the full definition (`name` 1–200 chars) / delete the flow and all its runs |
| POST | `/v1/flows/<flow_id>/patch` | Apply a graph patch operation (the canvas ↔ conversation protocol) |
| POST | `/v1/flows/<flow_id>/layout` | Recompute the layout (Sugiyama–Barycenter) and persist it |
| POST | `/v1/flows/<flow_id>/fork` | Copy a flow (usually a platform template) into the caller's flows (`{name?}`) |
| GET/POST | `/v1/flows/<flow_id>/versions` | List snapshots (oldest first) / create a named snapshot |
| POST | `/v1/flows/<flow_id>/versions/<version_id>/restore` | Restore the definition from a snapshot |
| GET | `/v1/flows/platform` | Platform (blueprint) flows |
| POST / PUT | `/v1/flows/<flow_id>/toggle-platform` · `/platform-meta` | Promote/demote a flow as a platform template / edit its name and description — admin (checked inside the handler) |
| GET | `/v1/flows/templates` · `/templates/<key>` | PROP-007 topology catalog (every declared shape; an unshipped one is listed greyed with its reason) / live preview of one template for the requested parameters (`source_profile_id` pre-fills slots) |
| GET | `/v1/flows/tools?profile_id=` | Tools and prompts a node can call — profile-scoped |
| GET/PUT | `/v1/flow-variables` | The caller's global flow variables / replace the whole set (`{"variables": {name: value}}`). Plaintext constants, **not** a secrets store |

#### 3.32.1. Execute a Flow

Starts a flow run and returns immediately with a `run_id`. Execution proceeds asynchronously in the background.

**Endpoint:** `POST /api/v1/flows/{flow_id}/execute`
**Authentication:** Required (JWT or access token)

**URL Parameters:**
- `flow_id` (string, required): The flow to run (`flow-<hex12>`).

**Request Body:** (all fields optional)

| Field | Type | Default | Description |
|---|---|---|---|
| `input_params` | object | `{}` | Key/value pairs merged into the flow's `global_params` at run start. Referenced inside nodes as `{name}` or `$name`. |
| `session_id` | string | `null` | Chat session to bind the run to. When supplied, it becomes the execution session for LLM nodes (in `compact` mode), inheriting that conversation's profile and history. **When omitted, the run auto-provisions a fresh session per run**, named after the flow (one new sidebar entry per run; runs are never folded into a prior session). Either way the bound session is returned as `session_id` and persisted on the run. |
| `node_session_mode` | string | `"compact"` | Accepted for compatibility but **not authoritative** — the flow's own `settings.parallelExecution` governs node session sharing (see note below). |
| `start_node_id` | string | `null` | Partial run anchor (debugging). Combined with `run_mode`. |
| `run_mode` | string | `"full"` | `"full"` — run the whole flow from the start node; `"single"` — run only `start_node_id`; `"downstream"` — run `start_node_id` plus everything reachable from it (seeded from the most recent run's node outputs). |
| `max_tokens_per_run` | integer | flow setting | Per-run token ceiling (input + output). When the ceiling is crossed, the run halts before the next dependency level / scope iteration and settles as `error` with a `budget_exceeded` marker. Falls back to the flow's own `settings.max_tokens_per_run`. |

**Success Response:** `202 Accepted`
```json
{
  "run_id": "frun-3a9c1b2d4e5f",
  "status": "queued",
  "status_url": "/api/v1/flows/runs/frun-3a9c1b2d4e5f",
  "session_id": "session-9f3b2a1c7d8e"
}
```
`status_url` mirrors the query Task contract (`{task_id, status_url}`) — poll it (§3.32.3) or open the stream form of it (§3.32.5) to monitor the run. `session_id` is the session the run is bound to (the one you passed, or a fresh per-run session named after the flow).

**Error Responses:**
- `400 Bad Request` — Invalid flow definition (e.g. no start node). Body: `{"error": "<reason>"}`.
- `400 Bad Request` — `{"error": "action_not_granted", "message": …}` (PROP-008 §6): an action node (`task_action`) whose agent is not granted its action, or that exists while OBDA-MODEL · OBDA-ACT is off. Nothing ran. The same refusal (`{"status": "error", "error": "action_not_granted", "message": …}`) answers every flow save (create · update · patch · import), so such a flow is never stored.
- `404 Not Found` — Flow not found / not owned by the caller.
- `500 Internal Server Error` — Execution could not be launched. Body: `{"error": "Execution failed", "detail": "<reason>"}`.

> **Note:** `202` means *accepted for execution*, not *completed*. The run is `queued` and may already be `running` by the time you poll. Always monitor via §3.32.4 or §3.32.5.

---

#### 3.32.2. List Runs for a Flow

Returns recent runs for a flow, newest first.

**Endpoint:** `GET /api/v1/flows/{flow_id}/runs`
**Authentication:** Required (JWT or access token)

**Query Parameters:**
- `limit` (integer, optional, default `50`): Maximum number of runs to return.

**Success Response:** `200 OK`
```json
{
  "runs": [
    {
      "run_id": "frun-3a9c1b2d4e5f",
      "flow_id": "flow-7b2e9d1a3c4f",
      "user_id": "a1b2c3d4-...",
      "status": "complete",
      "session_id": "session-9f3b2a1c7d8e",
      "input_params": { "region": "EMEA" },
      "output_result": { "...": "final node-output state" },
      "execution_trace": [ "...": "ordered list of run events" ],
      "tokens_input": 1820,
      "tokens_output": 540,
      "cost_usd": 0.0123,
      "started_at": "2026-06-17T08:30:01.114Z",
      "completed_at": "2026-06-17T08:30:09.882Z",
      "created_at": "2026-06-17T08:30:00.901Z"
    }
  ],
  "count": 1
}
```

**Error Responses:**
- `401 Unauthorized` — Authentication required.

> Runs are scoped to the authenticated user; you only ever see your own. An unknown `flow_id` returns an empty list, not a 404.

---

#### 3.32.3. Get a Run (status + per-node trace)

Returns the full run object plus a `node_runs` array — one record per node execution (including each loop-scope iteration). This is the primary **polling** endpoint.

**Endpoint:** `GET /api/v1/flows/runs/{run_id}`
**Authentication:** Required (JWT or access token)

**URL Parameters:**
- `run_id` (string, required): The run ID returned by §3.32.1.

**Success Response:** `200 OK`
```json
{
  "run_id": "frun-3a9c1b2d4e5f",
  "flow_id": "flow-7b2e9d1a3c4f",
  "user_id": "a1b2c3d4-...",
  "status": "complete",
  "session_id": "session-9f3b2a1c7d8e",
  "input_params": { "region": "EMEA" },
  "output_result": { "...": "final node-output state" },
  "execution_trace": [ "..." ],
  "tokens_input": 1820,
  "tokens_output": 540,
  "cost_usd": 0.0123,
  "started_at": "2026-06-17T08:30:01.114Z",
  "completed_at": "2026-06-17T08:30:09.882Z",
  "created_at": "2026-06-17T08:30:00.901Z",
  "last_updated": "2026-06-17T08:30:09.882Z",
  "result": { "...": "final node-output state" },
  "node_runs": [
    {
      "id": 1,
      "run_id": "frun-3a9c1b2d4e5f",
      "node_id": "node-start",
      "node_type": "start",
      "scope_id": null,
      "iteration": 0,
      "status": "success",
      "input_state": {},
      "output_state": {},
      "error_msg": null,
      "task_id": null,
      "session_id": null,
      "tokens_input": 0,
      "tokens_output": 0,
      "started_at": "2026-06-17T08:30:01.120Z",
      "completed_at": "2026-06-17T08:30:01.121Z"
    },
    {
      "id": 2,
      "run_id": "frun-3a9c1b2d4e5f",
      "node_id": "node-a1b2c3",
      "node_type": "task_llm",
      "scope_id": null,
      "iteration": 0,
      "status": "success",
      "input_state": { "...": "resolved references" },
      "output_state": { "output": "...", "tokens_input": 1820, "tokens_output": 540 },
      "error_msg": null,
      "task_id": "task-...",
      "session_id": "session-...",
      "tokens_input": 1820,
      "tokens_output": 540,
      "started_at": "2026-06-17T08:30:01.130Z",
      "completed_at": "2026-06-17T08:30:09.700Z"
    }
  ]
}
```

**Node-run `status` values:** `success`, `error`, `cancelled` (and `skipped` for an untaken decision branch). For LLM/prompt nodes, `task_id` and `session_id` cross-link the node to the query Task and chat session it spawned.

**Task-Object-compatible aliases.** Alongside the native fields, the response includes `last_updated` (the most recent lifecycle timestamp) and `result` (an alias of `output_result`) so a client written against the query Task Object (§4.1) can read a flow run without special-casing. The native fields are always present too.

**Error Responses:**
- `404 Not Found` — Run not found, or not owned by the caller (ownership failures also return `404`, not `403`, to avoid leaking existence).
- `401 Unauthorized` — Authentication required.

---

#### 3.32.4. Get Latest Per-Node Data

Returns each node's **most recent** execution data across the last 25 runs, keyed by `node_id`. Unlike §3.32.3 (which is scoped to one run), this aggregates across runs — so re-running a single node does not wipe other nodes' earlier results. Powers an inspector's per-node Data panel.

**Endpoint:** `GET /api/v1/flows/{flow_id}/node-data`
**Authentication:** Required (JWT or access token)

**Success Response:** `200 OK`
```json
{
  "run_id": "frun-3a9c1b2d4e5f",
  "status": "complete",
  "node_data": {
    "node-a1b2c3": {
      "node_id": "node-a1b2c3",
      "node_type": "task_llm",
      "status": "success",
      "input_state": { "...": "..." },
      "output_state": { "output": "...", "tokens_input": 1820, "tokens_output": 540 },
      "tokens_input": 1820,
      "tokens_output": 540,
      "started_at": "2026-06-17T08:30:01.130Z",
      "completed_at": "2026-06-17T08:30:09.700Z"
    }
  }
}
```

When the flow has no runs yet, returns `{"run_id": null, "node_data": {}}`.

**Error Responses:**
- `404 Not Found` — Flow not found / not owned by the caller.

---

#### 3.32.5. Subscribe to Live Run Events (SSE)

Server-Sent Events stream of a run's live progress. The stream opens immediately and is safe to subscribe to **before or after** `POST /execute` — a per-run **replay buffer** delivers any events emitted before you connected (deduplicated by sequence number). The stream sends a heartbeat comment every 30 seconds and **closes automatically** when the run reaches a terminal state.

**Endpoint:** `GET /api/v1/flows/runs/{run_id}/stream`
**Authentication:** Required. Provide either an `Authorization: Bearer <token>` header **or** a `?token=<jwt>` query parameter (browser `EventSource` cannot set headers, so use the query param there).
**Content-Type:** `text/event-stream`

**Event envelope** — each `data:` line is a JSON object:
```json
{ "type": "<event_type>", "payload": { "flow_id": "...", "run_id": "...", "...": "..." } }
```
Every payload is stamped with `flow_id` and `run_id` so a consumer can route by identity.

**Event types:**

| `type` | When | Notable payload fields |
|---|---|---|
| `flow_run_start` | Run begins | `input_params` |
| `flow_node_start` | A node begins | `node_id`, `node_type`, `label`, `scope_id`, `iteration` |
| `flow_node_complete` | A node succeeds | `node_id`, `status`, `output_preview` (≤200 chars), `tokens_input`, `tokens_output`, `cost_usd`, `extensions[]` |
| `flow_node_error` | A node fails | `node_id`, `error` |
| `flow_node_cancelled` | A node was cancelled mid-flight (not a failure) | `node_id`, `label` |
| `flow_node_skipped` | A decision branch was not taken | `node_id` |
| `flow_node_retry` | A transient LLM-node failure is being retried | `node_id`, `attempt`, `max_attempts` |
| `flow_node_warning` | Non-fatal warning (e.g. a decision condition could not be evaluated → defaulted to `false`) | `node_id`, `warning` |
| `flow_scope_start` / `flow_scope_iteration` / `flow_scope_complete` | Loop-scope lifecycle | `scope_id`, `iteration` |
| `flow_run_complete` | Run finished successfully — **stream closes** | `output_result`, `tokens_input`, `tokens_output`, `cost_usd` |
| `flow_run_error` | Run failed — **stream closes** | `error`, `node_id`, `budget_exceeded` |

**Heartbeat:** `: heartbeat\n\n` every 30 s of inactivity (an SSE comment — ignore it).

**Error Responses:**
- `401 Unauthorized` — No valid token in header or query param.
- `404 Not Found` — Run not found / not owned by the caller.

> **Scope of the per-run stream vs. the global channel.** This per-run stream carries the **full** granular animation (every node and scope event). The platform's per-user channel (`/api/notifications/subscribe`) receives **only** the run-lifecycle events (`flow_run_start` / `flow_run_complete` / `flow_run_error`) — enough to *discover* a run, not to follow it node-by-node. For detailed monitoring, use this per-run stream.

---

#### 3.32.6. Cancel a Run

Requests cooperative cancellation of an in-progress run. The signal halts the run before the next dependency level / scope iteration; an LLM node currently mid-generation is also aborted. If the run has already settled, the database status is still flipped to `cancelled`.

**Endpoint:** `POST /api/v1/flows/runs/{run_id}/cancel`
**Authentication:** Required (JWT or access token)

**URL Parameters:**
- `run_id` (string, required): The run to cancel.

**Request Body:** None

**Success Response:** `200 OK`
```json
{
  "cancelled": true,
  "run_id": "frun-3a9c1b2d4e5f",
  "signalled": true
}
```
`signalled` is `true` when a live executor was found and signalled; `false` when the run had already settled (status is still set to `cancelled`).

**Error Responses:**
- `404 Not Found` — Run not found / not owned by the caller.
- `401 Unauthorized` — Authentication required.

---

#### 3.32.7. End-to-End: Trigger and Monitor a Flow

```bash
# 1. Authenticate (access token recommended for automation; JWT also works)
JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}' | jq -r '.token')

# 2. Trigger the flow
RUN_ID=$(curl -s -X POST "http://localhost:5050/api/v1/flows/$FLOW_ID/execute" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"input_params": {"region": "EMEA"}}' | jq -r '.run_id')
echo "Run: $RUN_ID"

# 3a. Monitor by polling until terminal (complete | error | cancelled)
until curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:5050/api/v1/flows/runs/$RUN_ID" | \
  python3 -c "import json,sys; s=json.load(sys.stdin).get('status',''); exit(0 if s in ('complete','error','cancelled') else 1)" 2>/dev/null; do
  sleep 2
done

# 3b. (Alternative) Monitor live via SSE — note the ?token= form for header-less clients
#   curl -N "http://localhost:5050/api/v1/flows/runs/$RUN_ID/stream?token=$JWT"

# 4. Read the final result + per-node trace
curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:5050/api/v1/flows/runs/$RUN_ID" | \
  jq '{status, tokens_input, tokens_output, cost_usd, nodes: [.node_runs[] | {node_id, node_type, status}]}'

# 5. (Optional) Inspect the latest data for every node
curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:5050/api/v1/flows/$FLOW_ID/node-data" | jq '.node_data | keys'
```

---

### 3.33. MCP Server — Uderia as an MCP Provider (Tier 2.1)

Uderia ships a standalone **MCP server** that exposes Uderia agent profiles (and read-only
platform data) as MCP tools, so external MCP clients — Claude Desktop, Cursor, another agent —
can call a *governed* Uderia profile as a tool. The differentiator is trust: `ask_profile` runs
the full execution pipeline, so the answer passes through the profile's **grounding gate** and
**guardrails** before it is returned.

It is a thin facade over **this REST API** (it adds no new execution path); the bearer token is a
Uderia 90-day access token, and all per-user/profile/component governance is enforced server-side.

**Run it:**
```bash
# STDIO (an MCP client spawns it as a subprocess):
UDERIA_ACCESS_TOKEN=tda_… python -m trusted_data_agent.mcp_server

# Streamable-HTTP (serve at http://127.0.0.1:5055/mcp):
UDERIA_ACCESS_TOKEN=tda_… python -m trusted_data_agent.mcp_server --transport http --port 5055
```
`UDERIA_BASE_URL` (default `http://localhost:5050`) targets the instance.

**Consumer-tier tool segmentation.** `--profile user|developer|admin|all` (or `UDERIA_MCP_PROFILE`)
selects the tool surface per consumer perspective. It is a **hard gate**: the surface is clamped
to `min(launch profile, the access token's actual user tier)`, so a user-tier token can never
expose developer/admin tools. The full REST→tier mapping is in
[MCP_TIER_MATRIX.md](MCP_TIER_MATRIX.md).

**Tools (v1):**

| Tool | Tier | Wraps |
|---|---|---|
| `ask_profile(prompt, profile?)` | all | `POST /v1/sessions` → `/query` → poll `/v1/tasks/<id>` |
| `list_profiles()` | all | `GET /v1/profiles` |
| `list_knowledge_graphs()` | all | `GET /v1/knowledge-graph/list` |
| `list_collections()` | developer | `GET /v1/rag/collections` |
| `list_sessions()` | developer | `GET /v1/sessions` |
| `get_my_usage()` | developer | `GET /v1/consumption/summary` |
| `system_usage()` | admin | `GET /v1/consumption/system-summary` |
| `list_user_usage()` | admin | `GET /v1/consumption/users` |

Claude Desktop config (STDIO):
```json
{ "mcpServers": { "uderia": {
  "command": "python", "args": ["-m", "trusted_data_agent.mcp_server"],
  "env": { "UDERIA_ACCESS_TOKEN": "tda_…", "UDERIA_BASE_URL": "http://localhost:5050", "UDERIA_MCP_PROFILE": "user" }
}}}
```

Architecture: [docs/Architecture/MCP_SERVER_ARCHITECTURE.md](../Architecture/MCP_SERVER_ARCHITECTURE.md).

---

### 3.34. Agent Evaluation (Evals)

Programmatic access to the agent observability + evaluation system (WS3). Datasets/cases,
offline runs + scores, online quality spans, the cross-agent KPI dashboard, and per-turn scoring.
All require auth (own-scope). See [AGENT_OPS_EVAL_ARCHITECTURE.md](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md).

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/v1/evals/datasets` | List / create eval datasets |
| GET/POST | `/v1/evals/datasets/<id>/cases` | List / add cases |
| POST | `/v1/evals/datasets/<id>/mine` | Mine golden cases from anomalous sessions |
| GET | `/v1/evals/runs` · `/v1/evals/runs/<id>` · `/<id>/scores` · `/<id>/clusters` | Run list / detail / scores / failure clusters. **Requires `VIEW_ALL_SESSIONS`** — the offline EvalStore is instance-global ops data (runs carry no user column; the CLI writes them) |
| POST | `/v1/evals/gate` | Regression-gate a run (read-only check, CI exit code) |
| POST | `/v1/evals/score-turn` | Score a turn (deterministic + opt-in judges); persists a span |
| GET | `/v1/evals/turn-score` | Read a persisted turn score |
| GET | `/v1/evals/scoring-thresholds` | The **live** scoring bands, as the Quality Dimensions Guide needs them. The Guide previously stated the *shipped default* ("unset by default") — a constant wearing a live reading's clothes, and false in the direction that matters on any deployment whose admin has tuned the sliders |
| GET | `/v1/evals/online` · `/v1/evals/online/dashboard` | Tail online spans / aggregated quality dashboard. Both scoped: `?scope=my` (default, caller's spans only) or `?scope=system` behind `VIEW_ALL_SESSIONS` — spans carry session/turn ids and judge evidence, so they are never served cross-user unguarded |
| GET | `/v1/evals/agents/kpi-dashboard` · `/agents/<tag>/timeline` · `/agents/<tag>/runs` | Cross-agent KPIs / per-agent timeline / runs ledger |
| GET | `/v1/evals/library-outcomes?scope=my\|system&tag=<TAG>` | Read → ran per skill-library section across scored turns: per anchor `read_ran` · `read_failed` · `unread_ran` · `unread_failed` · `read_unused`, plus `read_first_rate`, `ran_after_read`, `ran_without_read` (`null` where that side has no statements). `system` scope requires `VIEW_ALL_SESSIONS`. |
| GET | `/v1/evals/agents/trust-behavior` | Behavioral Trust Score — per-agent fusion of Trust-Membrane integrity with the live track record (`?scope=my\|system`; `enabled` mirrors the admin `trust_behavior` block). Response carries `breaker_mode` (off\|notify\|freeze_autonomy) and, in system scope, `self_uuid`; system-scope cells are keyed **`tag::uuid`** (stamped with `tag`/`user_uuid`) so two users' same-tag agents never merge, and the requester's integrity caps only the requester's own cells |
| POST | `/v1/evals/backtest` | Policy backtest — replay a proposed grounding/guardrail policy over the user's persisted turns (deterministic, 0 tokens, read-only) |
| GET | `/v1/evals/diff` | Diff two runs |
| GET/POST | `/v1/evals/annotations` | List / record annotation verdicts. POST body: `{label: agree\|disagree, note?, case_id?, session_id?, turn?, dimension?, judge_passed?}` — `dimension` + `judge_passed` (the ruling the human saw, frozen at annotation time) make the row a κ pair; without them it is a note. **Requires `VIEW_ALL_SESSIONS`** — annotations feed judge calibration (κ), so recording is an ops act |
| GET | `/v1/evals/judge-calibration` | Per-dimension judge↔human calibration over the annotation queue: Cohen's κ, raw agreement, confusion, and `status` ∈ `calibrated` (κ ≥ 0.6 over ≥ 10 pairs — ELIGIBLE for promotion to gating; nothing is promoted automatically) \| `below_floor` \| `insufficient` (under the pair floor: κ reported, never cleared). Same gate as the store |
| GET | `/v1/evals/judge-queue` | Recent judge rulings awaiting a human verdict (`?limit=`, cap 200): reads the online quality spans (deduped, newest first), keeps only registry-JUDGE dimensions with a non-abstained ruling, marks rulings already annotated by (session, turn, dimension). Each row carries exactly the fields the annotation POST needs to freeze the pair. Same gate as the store |

CLI alternative: `python -m trusted_data_agent.eval.cli run|gate|mine|report|list`.

**`POST /v1/evals/backtest`** body — each policy section optional (at least one required):

```json
{
  "policy": {
    "grounding_mode": "enforce",
    "outbound": {"mode": "enforce", "pii": true, "toxicity": true},
    "inbound": {"mode": "enforce"}
  },
  "sessions": ["optional-session-ids"],
  "profile_tag": "OPTIM",
  "max_sessions": 20,
  "max_turns": 300
}
```

Returns per-section counts + capped samples (`would_redact`, `would_block`,
`would_escalate_judge`, …). Guardrail projections replay the exact live policy
engine on stored text; grounding projections are trigger-level from the recorded
gate stamp — the LLM judge is honestly **not** simulated. Samples never carry raw
PII values.

**`trust_behavior` admin block** (on `GET/POST /v1/admin/app-config`): `enabled`
(default false — gates the roster ring/posture surfaces only), `window_turns`,
`min_turns_proven`, `min_turns_trusted`, `healthy_floor`, `trusted_floor`,
`at_risk_floor`, `half_life_days`, `stale_days`, `breaker_mode`
(`off|notify|freeze_autonomy`). Persisted to `system_settings`, restored at startup.

**`kg_config` admin block** (on `GET/POST /v1/admin/app-config`):
`{"semantic_seeding": bool}` — the second, semantic recall channel for knowledge-graph
retrieval. **Default true.** When on, each bound graph's entities are embedded once
(the configured `RAG_EMBEDDING_MODEL`) and the query's nearest entities by meaning join
the lexical candidate pool, so a question in business vocabulary still reaches the right
node; lexical matches stay authoritative (the semantic bonus is capped below an exact
identifier match). A graph with no index yet answers lexically and builds in the
background — a turn is never delayed by it. Applied at runtime (no restart), persisted
to `system_settings`, restored at startup. Also settable per deployment via
`TDA_KG_SEMANTIC_SEEDING` / `TDA_KG_SEMANTIC_INDEX_DIR`; the stored admin choice wins
over the environment default. Surfaced in Live Status on the `kg_enrichment` event
(`enrichments[].semantic_seeding`: `state`, `hits`, `bridged[]`, `bridged_dropped`). The scored dimension `kg_seed_utilization` (informational) reads the fraction of `bridged[]` entities the turn's data tool calls then referenced; the same rule records `used` into `kg_entity_usage`.

**`agent_eval.obda_policy`** (on the same `GET/POST /v1/admin/app-config`, Oct 2026): the
deployment's "contracts only" policy, one rung per plane — `{"read": …, "model": …, "act": …}`,
each `off` · `observe` · `enforce` (`core/posture_modes`; an unknown rung normalises to `off`).
Live on POST (applied once per turn at `agent_authority.derive_principal`); persisted with the
`agent_eval` block, restored at startup. `enforce` writes the policy INTO every agent's scope
(an exclusive data surface, an exclusive write surface, command tools refused; the knowledge-graph
tool refuses direct edits under `model`); `observe` records `policy_observed` trust events instead
of refusing. **Availability is not here:** the three planes are the components `obda_read` ·
`obda_model` · `action` (Administration → Components, `GET/POST /v1/admin/component-settings`), and
the former `agent_eval.obda_enabled` / `act_enabled` masters were migrated once into component
availability at startup — the keys are inert and no longer served.

**`agent_eval.station_reporting`** (on the same `GET/POST /v1/admin/app-config`,
PROP-002b): per-station reporting levels `{<station>: "all"|"warning"|"danger"}` —
what each station SURFACES in the Assurance Inbox, badge and ring colour, assigned
from the ONE severity construct (`assurance_points.SEVERITY_CONSTRUCT`). Danger
always surfaces (the floor, not assignable); levels filter presentation, never
creation. Server-normalized on read and write; persisted with the `agent_eval`
block, restored at startup.

**`judgeConfig`** (per-profile, saved with the profile — PROP-002 follow-up): the
JUDGED station's per-agent settings — `judges` (`inherit|on|off` vs the platform
judge default; the `ONLINE_EVAL_ENABLED` scoring master always wins), `sampleRate`
(clamped [0,1] override, `null` = platform rate), `inboxOnCondemnation` (default
true — a delivered answer a groundedness judge convicts files a danger
`judge_condemnation` incident). Portable, unsigned (measurement config).

**Assurance Inbox & tooling** (all own-scope, auth):

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/assurance/inbox` | Trust incidents — `stored` (breaker/memory-quarantine, ack-able) + `derived` (grounding/guardrail/honesty/session-sentinel, read live from the persisted trace; annotate-level acts and honesty flags derive too, and each item carries `acknowledged` when a derived ack exists). PROP-002b: also derives one **`station_shortfall_<station>::<tag>`** danger observation per (agent, station) whose execution sits in the danger band (worst-owned-dimension vs the shared review floor), and returns **`station_reporting`** — the per-station reporting levels (`all`/`warning`/`danger`; danger always surfaces) that also filter the `unacked` badge count server-side. `?include_acked=true`, `?sessions=N` |
| POST | `/v1/assurance/inbox/<id>/ack` | Acknowledge a stored event |
| POST | `/v1/assurance/inbox/derived/ack` | PROP-002 (completeness invariant): acknowledge a DERIVED observation or the session sentinel. Body `{"session_id", "turn_id" (null = session-level), "event_type"}`. The sealed trace is never rewritten — the ack is a stored disposition about it |
| GET | `/v1/assurance/inbox/count` | Unacknowledged count (badge) |
| POST | `/v1/assurance/resilience-test` | Red-team self-test — curated attack corpus through the real deterministic detectors → per-category recall + benign false-positive rate + resilience score (0 tokens, no live model) |
| POST | `/v1/assurance/audit-pack` | Signed evidence bundle — posture + eval aggregates + incidents + resilience test + compliance-control map, Ed25519-signed with the deployment provenance key (offline-verifiable). Per-profile `posture` carries the RESOLVED mode of **all five gates** (`gates{grounding,corpus,honesty,outbound,inbound}` each with its `source` ∈ gate/agent/grounding/default) from the one resolver (`effective_posture`), so an agent hardened via agent-level inheritance is not understated as `observe` |
| GET | `/v1/assurance/reviews` | HITL review queue — withheld originals parked by `review` mode as OWED work, plus plain Enforce's **recovery ledger** (`retained` = a full withholding's original kept recoverable, no disposition owed, never counted in `pending`). Owner-scoped, widened by **four eyes** (PROP-008 §5.4a): the listing also carries parked writes from OTHER people's agents that await the caller as designated approver (`owner` = that agent's owner, by account name). Every item carries `approvers` (ids), `can_decide` (this caller may approve/reject it), `decided_by`, and — when designated — `awaiting` (account names); an owner's own write that awaits someone else is listed with `can_decide: false`. `pending` counts only what THIS caller owes; `?status=pending\|approved\|rejected\|retained\|all`, `?include_content=true`. Response carries `pending` (owed) and `retained` (ledger) counts. Retention bounds: settled + retained items age out after 90 days and `retained` is capped per user (oldest first); `pending` is never auto-pruned |
| POST | `/v1/assurance/reviews/<id>/approve` · `/reject` | Disposition an open item (`pending` or `retained`; optional `{note}`). **409** = the item is not open (a settled fact) · **503** = the review store failed and NOTHING was recorded (retry) — a store failure is never reported as a settled disposition; an audit `review_decision` event is appended to the originating turn and a `review_decision_notice` notification is broadcast. Release is **append-only**: delivered history is never rewritten — an approved original surfaces in the turn's answer capsule, labelled as a human release. **403 `not_your_decision`** (with `awaiting`) = the item awaits a designated approver and the caller is not one — the agent's owner cannot approve it; refused before anything runs. **A parked ACTION** (`source: "action"`): approve PERFORMS the parked write first — as the OWNER's agent on the owner's connection, with the approver named in the ledger and act log (`approved_by`); the write and its disposition run as one unit shielded from a client disconnect — (the contract, its signature, the statement and invocation hashes and the agent's grant re-checked) and only then records the approval — a refused perform answers `409 action_not_performed` and leaves the item open; a performed one returns `action_record` (outcome, rows, invocation hash) and is recorded as the act's confirmation on its turn (`turn['action_confirmations']`), so the answer row, the capsule and BOUNDED read it as performed; a rejection writes nothing. A write is never bulk-approved in the UI. Approve additionally accepts `{"release_to_conversation": true}` (default false = record only): the owner explicitly releases the original **into the conversation** — subsequent turns receive it via the `review_release` context module (clearly labelled as a human decision) and its exact PII values join a session-scoped outbound guardrail allowlist; the release is recorded as a `review_release` trust event and echoed as `released_to_context` on the item, the audit event, and the notification |
| GET | `/v1/assurance/trust-history/<tag>` | Trust changelog for one agent — sign-offs, stored incidents, review items + decisions, **trip resets**, and **every signed action it performed, parked, observed or had refused** (`kind: "act"`, `face`, `outcome`, the approver by account name; read from the platform act log, so a flow, schedule, tile or MCP act appears even though no turn holds it), merged newest-first (audit-facing) |
| POST | `/v1/assurance/trust-trip/<tag>/reset` | **Start a new leg** (the trust-trip reset, speedometer model — `TRUST_TRIP_RESET.md`). Appends an audited per-agent marker (who/when/`{note?}`/the agent's integrity state at that moment) and deletes NOTHING — spans, verdicts and incidents stay; the whole-record lens is untouched. Owner-scoped (the tag resolves against the caller's own profiles; 404 otherwise). The dashboard's `by_agent` cells then carry a `trip` sub-cell (same shape as the cell itself — the same aggregator over the leg's spans — plus `since`/`note`/`leg_n`/`integrity_state` and a `prev` previous-leg sub-cell) that the Trust Circle's *This leg* lens reads |
| GET | `/v1/assurance/trust-trip/<tag>` | The leg logbook — every marker ever set (append-only, newest first) + the current leg |
| GET | `/v1/assurance/agent-story/<tag>` | **The life of this agent** — the trust story assembled from records that already exist (profile birth, corpus scans, attestation, span window, review dispositions, acked incidents, trip resets) and narrated as chapters around the ring. Owner-scoped; every source read defensively (an absent subsystem drops its chapter rather than failing). Nothing is generated or estimated — every sentence is backed by a stored record |
| POST | `/v1/assurance/live-redteam` | **Live** red-team probe — drives a bounded adversarial corpus as REAL turns through a target profile and scores whether the guards held (injection obeyed? planted PII echoed? scope drift? surface escape? **write escape** — an action-only agent lured into a raw write, adjudicated from the SQL gate's `action-only` denial; the lure is a self-assignment on an impossible key, so even an escape changes no row). Optional `probe_ids: [str]` runs only the named probes (corpus order, still capped by `max_probes`); none of them existing → `400`. Unlike `/resilience-test` (0 tokens, detectors only) this **costs real tokens** — one turn per probe — so it is opt-in and hard-capped by `max_probes`. A failure lands in the Assurance Inbox and broadcasts live |
| POST | `/v1/assurance/retrieval-anomaly` | **Index-poisoning detection** — per-document retrieval frequency × query diversity over the caller's recent persisted turns (no new logging; bounded window). A high-frequency, high-diversity document is a suspicious retrieval hub, flagged for HUMAN disposition — honestly labelled statistical, **never auto-quarantined** |
| GET | `/v1/assurance/vocabulary` | Every assurance mechanism **and property** as Guide-shaped entries. A `cls:"property"` entry carries its `lanes` (the module bound per lane) and `dims` — the scored dimension that grades it, DERIVED here from each scorer's own `property` declaration, since this is the one layer holding both registries (the substrate must not import `eval`). **Served, not derived on the client**, so the Assurance Guide is COMPLETE from any entry point — registering only what a rendered dial happened to list left the Guide empty when opened from the Quality card |

---

### 3.35. Knowledge Graph Management

The core KG CRUD + lifecycle API (distinct from the KG *Marketplace*, §3.28). KGs are per-profile;
all require auth. Constructors, layering, and the inspector are described in
[ONTOLOGY_ARCHITECTURE.md](../Architecture/ONTOLOGY_ARCHITECTURE.md).

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/knowledge-graph/list` | List the user's knowledge graphs |
| GET | `/v1/knowledge-graph/stats` | Entity/relationship counts for the active KG |
| GET/POST | `/v1/knowledge-graph/entities` | List / add entities |
| PUT/DELETE | `/v1/knowledge-graph/entities/<id>` | Update / delete an entity |
| GET/POST | `/v1/knowledge-graph/relationships` | List / add relationships |
| DELETE | `/v1/knowledge-graph/relationships/<id>` | Delete a relationship |
| GET | `/v1/knowledge-graph/{subgraph,schema-overview,node-neighborhood,graph-spec}` | Inspector reads (typed subgraph, meta-graph, neighbourhood, render spec) |
| GET | `/v1/knowledge-graph/context` | Test context enrichment for a query |
| POST | `/v1/knowledge-graph/generate` · `/discover` | Build a KG (constructor) / `501` — schema discovery not implemented |
| POST | `/v1/knowledge-graph/check-update` · `/apply-update` | Diff source vs KG / rebuild in place. Both accept `options_override` (incl. `validate_with_source`); a check keeps its rebuilt candidate for 30 min so the apply that follows does not rebuild (or re-validate) a second time. `apply-update` stamps `source_validation` on the graph's metadata from the candidate's content (set when the rebuild validated, cleared when it did not); `GET /v1/knowledge-graph/list` returns it per graph as `{validated_at, tables, keys_checked, keys_not_unique, shapes, date_ranges, blank_ratios, probe_errors, seconds}` — the cards' *Source-validated · date* badge. | An ANDP graph's `source_label` names its product(s) (`ANDP: NIBRS_Source`), an industry-model graph its model; the Update dialog reads it as the resync source. A pack export carries each graph's stamp (`knowledge_graphs/kg_N.json: source_validation`) and every import path applies it — verbatim with the publisher's `validated_at`, else derived from stamped nodes, else cleared.
| GET/POST | `/v1/knowledge-graph/versions` · `/<id>/restore` | Version history / snapshot / restore |
| GET/POST/DELETE/PATCH | `/v1/knowledge-graph/assignments[...]` | Per-profile KG assignment management |
| POST/DELETE/PATCH | `/v1/knowledge-graph/{import,clear,rename}` | Bulk import / clear / rename |
| GET | `/v1/knowledge-graph/export` | Export a KG (file) |
| GET | `/v1/knowledge-graph/industry-models[/<industry>/structure]` | List / inspect Databricks industry models |
| GET | `/v1/knowledge-graph/<kg_id>/lifecycle` · `/conformance` | Tier 4.3 governance: the graph's lifecycle state / its conformance report |
| POST | `/v1/knowledge-graph/<kg_id>/lifecycle/<action>` | `propose` · `approve` · `reject` · `publish` · `deprecate` (conformance-gated; audited). **`publish` also materialises the graph's released view** `<kg_id>::released` (Model B, below) |

**Model B — released-only grounding (Sep 2026).** A profile whose `componentConfig.knowledge_graph.groundOn` is `"released"` (set via `PUT /v1/profiles/<id>` — send the whole `componentConfig` block; absent/any other value = the live draft) reads the ontology's PUBLISHED version for every KG read. The released view is a hidden shadow graph: `GET /v1/knowledge-graph/list` never lists a `…::released` graph, and pack export never bundles one. The turn's `kg_enrichment_event.enrichments[]` carries `grounding` — `{mode: "live"}`, or `{mode: "released", kg_id, version_id, label, lifecycle}`, or `{mode: "released", fallback: "live", reason}` when no release exists — beside `kg_id`, `kg_name` and `entity_names` (the rendered entities). `TDA_KnowledgeGraph` write actions (`add_entity`, `add_relationship`, `restore`, `declare_concept`) through a released-mode agent return a refusal payload (`metadata.refused: "released_read_only"`).

**KG Store backends** (`/v1/kg-store/*`) — pluggable SQLite/Neo4j backends, per-profile resolution:

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/kg-store/backends` | Backends allowed for the user's tier |
| GET/POST | `/v1/kg-store/configurations` | List / create backend configs |
| GET/PUT/DELETE | `/v1/kg-store/configurations/<id>` | Read / update / delete a config |
| POST | `/v1/kg-store/configurations/<id>/test` | Test a backend connection |
| GET/PUT | `/v1/kg-store/settings` | KG governance settings (admin) |

---

### 3.36. Database Connections

A centralized, per-user database-connection store (used by Canvas execute and PDM deploy). Secrets
are Fernet-encrypted; reads return masked metadata. Surfaced as the `uderia-database` connector.

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/v1/database-connections` | List / create connections |
| GET/PUT/DELETE | `/v1/database-connections/<id>` | Read / update / delete |
| POST | `/v1/database-connections/<id>/test` · `/test` | Test a saved / unsaved connection |
| POST | `/v1/database-connections/migrate-legacy` | Migrate legacy `canvas_conn_*` connections |

#### 3.36.1. Data sources and combining rules (PROP-006)

An agent may read several sources (its MCP server plus the Database Navigator's connections) and,
when the deployment allows it, combine their results in one read-only SELECT run by code
(`TDA_Compute` `op: "sql"`, switch `TDA_COMPUTE_SQL`, effective only under `TDA_COMPUTE_LINEAGE`).
A **source id** is `mcp:<server id>` or `conn:<connection id>`; an **endpoint** is the system it
reaches, derived by code and never a credential — `mcp:<host>:<port><path>` (or
`mcp:stdio:<command> <args>`), `db:<driver>://<host>:<port>/<database>`, `db:<driver>:file:<path>`.

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/v1/data-sources` | The profile editor's Data sources section: the caller's MCP servers and connections with their endpoints, the administrator's rules mapped onto the caller's own sources, and whether combining is available | jwt |
| GET | `/v1/admin/source-endpoints` | Every endpoint any user's servers and connections reach — names as users gave them and how many users reach each; what a deployment rule is written against | admin |

`GET /v1/data-sources` response:

```json
{
  "status": "success",
  "mcp_servers": [{"source_id": "mcp:s1", "id": "s1", "kind": "mcp", "name": "Teradata MCP", "endpoint": "mcp:uderia.com:8888/mcp"}],
  "connections": [{"source_id": "conn:hr", "id": "hr", "kind": "database", "name": "HR warehouse",
                   "driver": "teradata", "endpoint": "db:teradata://hr-host:1025/hr", "queryable": true}],
  "admin_rules": [{"endpoints": ["db:teradata://hr-host:1025/hr", "db:postgresql://crm:5432/crm"],
                   "reason": "HR data never meets CRM data",
                   "yours": {"db:teradata://hr-host:1025/hr": ["conn:hr"], "db:postgresql://crm:5432/crm": []},
                   "applies": false}],
  "combine": {"available": true, "reason": "", "incomplete_inputs": "refuse"}
}
```

`queryable` is true only for a driver the Database Navigator reads directly (`DIRECT_DRIVERS`).
`applies` says whether a rule binds two or more of the caller's own sources. When combining is off,
`combine.reason` names the switch.

**Deployment rules** live in the app configuration (`GET/POST /v1/admin/app-config`, key
`combine_isolation`): `[{"endpoints": [endpoint, endpoint, ...], "reason": "..."}]`. They hold for
every user who reaches those systems, persist in `system_settings`, and are edited in
Administration → Trust → Enforcement → Combining rules.

**An agent's own rules** are the profile key `combineIsolation`
(`POST /v1/profiles`, `PUT /v1/profiles/<id>`): `[{"sources": [source id, source id, ...], "reason": "..."}]`.
They only ADD restrictions. Validated at the door — a rule must name two or more different source
ids (`mcp:…` / `conn:…`); otherwise `400 {"error": "invalid_combine_isolation"}`. `[]` clears them.
The key is instance-local (it names this instance's source ids), so an agent pack never carries or
overwrites it; it enters the agent's **deployment** trust half only when non-empty.

---

### 3.37. Platform Connectors & Connector Registry

Admin-governed capability connectors (distinct from user MCP **data-source** servers, §3.9) and the
registry they are installed from. Management endpoints are admin-gated; reads are available to the
owning user. See [CONNECTOR_ARCHITECTURE.md](../Architecture/CONNECTOR_ARCHITECTURE.md).

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/v1/platform-connectors` · `/<id>` · `/<id>/tools` | List / read connector / its tool schemas | jwt |
| PUT/DELETE | `/v1/platform-connectors/<id>` | Update governance / delete | admin |
| POST | `/v1/platform-connectors/remote` · `/<id>/refresh-tools` | Add remote MCP connector / re-discover tools | admin |
| GET | `/v1/connector-registry/sources` · `/servers` | List registry sources / browse connectors | jwt |
| POST | `/v1/connector-registry/sources` · `/v1/connector-registry/servers/install` | Add a registry source / install a connector from one (`{source_id, server_id, server_data}`, `201`) | admin |
| DELETE | `/v1/connector-registry/sources/<source_id>` | Remove a non-builtin registry source | admin |

OAuth connect/callback/status for user connectors is documented in §3.30.

---

### 3.38. Component Instances & Knowledge Repositories

**Component instance portability** (export/import a specific flow/dashboard/canvas instance, §
COMPONENT_ARCHITECTURE):

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/components` · `/<id>` · `/manifest` | List installed components / detail / manifest |
| GET/POST | `/v1/components/<cid>/instances/<id>/export` · `/instances/import` | Export / import an instance (file) |

**Knowledge repository documents** (`knowledge_routes.py`) — document/chunk management for the
corpora `rag_focused` profiles retrieve from (the repository *collections* themselves are managed
under §3.15 / `/v1/rag/collections`):

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/knowledge/repositories/<id>/search` | Semantic search across a repository (read) |
| GET/POST/DELETE | `/v1/knowledge/repositories/<id>/documents[/<doc_id>]` | List / add / delete documents |
| PATCH | `/v1/knowledge/repositories/<id>` | Update settings: sync interval/anchor, source root, model lock, sensitivity, the post-sync standing policy (`sync_auto_ack_findings`, `sync_auto_resign`) |
| GET | `/v1/knowledge/collections/<id>/chunks[/<chunk_id>]` | Inspect chunks |
| POST | `/v1/knowledge/preview-chunking` · `/test-connection` | Preview chunking / test backend |
| POST/GET | `/v1/knowledge/repositories/<id>/hybrid/{enable,status}` | Enable / status of BM25 hybrid search |

> **Note (REST→MCP):** which of these endpoints are exposed by the MCP server, and at which
> consumer tier, is tabulated in [MCP_TIER_MATRIX.md](MCP_TIER_MATRIX.md).

---

### 3.39. Trust Membrane (Tier 5.2)

Trust as a **visible, cryptographically-verifiable badge on the agent** — computed compositionally over the agent's ingredients (a signed agent contains only signed ingredients). Design: `TIER_5.2_TRUST_MEMBRANE.md`. All endpoints require auth (`/api` prefix).

> These `/v1/trust/*` endpoints (and `/v1/platform-ontology/*`) are part of the **Ontology Suite** — one physically-isolatable enterprise feature (Platform Ontology UX + Trust Membrane + the REST for both), gated by a single switch (`core/enterprise.py`). When that switch is off, the whole family **404s**. See [PLATFORM_ONTOLOGY_ARCHITECTURE.md §11](../Architecture/PLATFORM_ONTOLOGY_ARCHITECTURE.md).

| Method | Path | Description | Tier |
|---|---|---|---|
| `GET` | `/v1/trust/agent/<profile_id>` | The agent's live trust badge + per-ingredient breakdown (`state ∈ unsigned\|trusted\|changed`, `ingredients[]`, `blocking[]`) | any (own profile) |
| `POST` | `/v1/trust/ontology/<kg_id>/sign-off` | Validate an ontology ingredient via the conformance gate, then sign it. **422** if nonconformant. | signer (admin) + **owner** |
| `POST` | `/v1/trust/skill/<skill_id>/sign-off` | Sign a skill ingredient (admin-attested) | signer (admin) |
| `POST` | `/v1/trust/knowledge/<collection_id>/sign-off` | Sign a knowledge repository. **Earned signature**: blocked while any document is unscreened, or carries an injection finding neither quarantined nor acknowledged | signer (admin) + **owner** |
| `POST` | `/v1/trust/planner/<collection_id>/sign-off` | Sign a planner (champion-case) repository | signer (admin) + **owner** |
| `POST` | `/v1/trust/flow/<flow_id>/sign-off` · `/v1/trust/dashboard/<dashboard_id>/sign-off` · `/v1/trust/canvas/<canvas_id>/sign-off` | Sign a workspace **component instance** (code / query / markup validated by its kind's validator) | signer (admin) + **owner** |
| `POST` | `/v1/trust/extension/<extension_id>/sign-off` | Sign an extension (code + content scan). Platform-level artefact — tier-gated only, no owner exists | signer (admin) |
| `POST` | `/v1/trust/agent-pack/<pack_id>/sign-off` | Sign an agent pack — **aggregate** over its member profiles; blocked while any member is unsigned | signer (admin) + **owner** |
| `POST` | `/v1/trust/agent/<profile_id>/sign-off` | Sign the agent — the **bottom-up gate**: **409** with `blocking[]` unless every ingredient is already signed | signer (admin) |
| `GET` | `/v1/trust/agents` | **Every one of the caller's agents with its live badge** — one call for the Trust Circle / roster / posture matrix. Rows carry `state`, `portable_state`, `deployment{state,required,drift[]}` (the two halves — a `changed` face routes a *re-attest* rather than a *re-sign*), `composition_state`, `blocking[]`, `ingredients[]`. Resolves per profile and **degrades per agent** (a failed resolve reads `unsigned` — the weakest claim, never a false 🟢) | any (own agents) |
| `GET` | `/v1/trust/artifact/<kind>/<artifact_id>` | Badge state of a single artefact → `{kind, artifact_id, state}`. **400 `unknown_kind`** (with `known_kinds[]`) for an unrecognised kind — never answered for, since a plausible `unsigned` is indistinguishable from a real never-signed artefact | any (own artefact) |
| `POST` | `/v1/trust/artifacts` | Batch badge states. Body `{items:[{kind,id}]}` → `{states:{"kind:id": state}}`; unrecognised kinds are returned in `unknown_kinds[]` and skipped, the rest still served | any |
| `POST` | `/v1/trust/agent/<profile_id>/attest-deployment` | Attest **this instance's** resolved capability surface (model · reachable MCP tools/prompts · connector opt-in). Independent of the artefact sign-off and of the ingredient gate, so a subscriber can attest a deployment of a pack somebody else signed. **409 `unresolved_surface`** when a surface of the live deployment signature cannot be resolved — a never-classified tool catalog (`"unresolved": ["catalog"]`: run the profile's connection test / `POST /v1/profiles/<id>/reclassify` first), an unreadable connector store, etc.; the `error` field carries the remedy verbatim. An attestation is never written over an unresolved surface. | **owner** of the profile (any tier) — the profile is resolved under the caller's own uuid, so a non-owner gets 404 |
| `POST` | `/v1/trust/verify` | Offline-verify a stored attestation's Ed25519 signature. Body `{kind, artifact_id}` → `{found, signed, verified, fingerprint, version_hash, key_source, key_status}`. `key_source ∈ carried\|local` names WHICH public key verified (the signer's carried key vs this instance's) — verification proves internal consistency with that key, not publisher identity (trusted-publisher pinning is a deferred policy). `key_status ∈ active\|retired\|carried\|unknown` (PROP-009): a signature made with an earlier key of this deployment still verifies and reads `retired`. For an agent (owner-scoped) the caller's own attestation is verified | any |
| `GET` | `/v1/trust/history/<kind>/<artifact_id>` | **Attestation history** (PROP-009 §4.1): every recorded event for the artefact, newest first — `sign` · `reinstate` · `revoke` · `retire` · `migrated` · `assigned` — each with hash, signer, key fingerprint, `key_status`, `signed_at`, `recorded_at`, `actor_uuid`. A re-sign never erases an earlier signature; it stays here. For an agent, the caller's own history only. **400** for an unknown kind | any (own artefact) |
| `GET` | `/v1/trust/unassigned` | **Admin.** Agent attestations the owner-scoping migration could not attribute, each with `holders[]` and `reason` in words (*"no account holds this agent any more (its profile was deleted)"* · *"N accounts hold this agent id and none of them signed it"*). Listed for review, never assigned by guess; no badge reads them | admin |
| `POST` | `/v1/trust/unassigned/assign` | **Admin.** Body `{kind, artifact_id, owner_uuid}` — give an unassigned agent attestation to the account that holds that agent. **409 `not_held`** if that account holds no agent with this id; **409 `not_assignable`** if the row is not unassigned or the account already has one. Recorded in history as `assigned` | admin |
| `GET` | `/v1/trust/keys` | Every public key that has signed on this deployment: `{fingerprint, public_key_pem, origin, status ∈ active\|retired\|carried, first_seen, last_seen, retired_at}`. A new local key retires the previous one; a key seen on a reinstated attestation is `carried` | any |

| `GET` | `/v1/evidence/turn/<session_id>/<turn>` | **Evidence for one answer** (PROP-009 §4.3): every signed part the turn used — `groups[]` in order *Agent · Deployment · What the agent is made of · Data read · Acts performed*; each item `{role, kind, kind_label, artifact_id, label, then, now, status}` where **`then`** comes only from the turn's sealed bill of materials (state, content hash, attested hash, `signer{signer_name, signed_at, key_fingerprint, key_status}`) and **`now`** is resolved live; `status ∈ unchanged\|changed_since\|not_found\|no_longer_used\|recorded`. Plus `summary{parts, words, attention, not_signed_when_answered[], changed_since[], not_found_now[]}` and `chain{valid, chain_valid, content_valid, errors[], key_fingerprint, key_status}` (the turn's own chain and content, re-verified). A turn from before evidence recording returns `recorded:false` with *"parts not recorded — this answer predates evidence recording"*; a turn whose session file is gone is served from the content-free evidence record with `session.available:false`. **404** for a turn the caller does not have | any (own turns) |
| `GET` | `/v1/evidence` | **Every current signature the caller may see, read now, worst first** (PROP-009 §5.3): `rows[]` each `{kind, kind_label, artifact_id, label, state_now, signed, signer_name, signed_at, key_fingerprint, key_status, history_id, answers, since_current, flags[], worst}` with `flags ⊆ {changed, retired_key, not_signed}`, and `summary{signatures, changed, retired_key, attention, words}` (*"78 signatures · 24 changed since signed"*). Visibility: the caller's own agents, the artefacts the Trust Membrane's ownership gate grants them (an ownership error hides the row), and platform-wide kinds; `?scope=system` lists every account's (**403** without `VIEW_ALL_SESSIONS`). Filters `?kind=`, `?state=` (a flag or a worst level — narrows the rows, never the summary), `?q=` (label search) | any (own); `VIEW_ALL_SESSIONS` for `scope=system` |
| `GET`/`POST` | `/v1/evidence/ledger` | **The evidence ledger, verified now** (PROP-009 §4.5): `{intact, entries, backfilled, first_live_seq, tip_seq, tip_hash, breaks_count, edited_count, removed_count, unledgered_count, checkpoints[], checkpoint{seq, tip_hash, payload, signed, signature, key_fingerprint, at}, custody{signer, key_on_host, available, fingerprint}, anchoring{enabled, sink, interval_seconds, anchors, last_seq, last_at, last_ok, last_error}, words{words, attention}}`. Every tip published outside this host (PROP-009 §4.6) is re-proved on each read; a failed last anchor reads as `warn`. POST `{checkpoints:[{seq, tip_hash}]}` (from earlier exports) also reports whether the ledger still reaches each one (`ok`, `why`: *no longer reaches* / *rewritten since*). The lists naming which records moved (`breaks`, `edited`, `removed`, `unledgered`) are returned only with `VIEW_ALL_SESSIONS` | any; detail with `VIEW_ALL_SESSIONS` |
| `POST` | `/v1/evidence/ledger/anchor` | **Publish a signed checkpoint of the ledger tip now** (PROP-009 §4.6) to the configured sink (`TDA_EVIDENCE_ANCHOR` = `file` \| `webhook` \| `rfc3161`, target `TDA_EVIDENCE_ANCHOR_TARGET`). Body `{force}`. Returns `{anchored, seq, tip_hash, sink, error}` or `{anchored:false, reason}` when the same tip was already published. **409** `anchoring_off` when unset; **403** without `VIEW_ALL_SESSIONS` | `VIEW_ALL_SESSIONS` |
| `POST` | `/v1/evidence/export` | **A portable evidence bundle** (PROP-009 §4.4), verifiable offline with `scripts/verify_evidence.py` or `static/trust/verify-evidence.html`. Body `{turns:[{session_id, turn}], artifacts:[{kind, artifact_id}], content:"limit"\|"all"\|"none", preset:"answer"\|"audit"\|null, include_receipt, include_audit_pack, title}`. Returns `{format:"uderia.evidence.bundle", version, deployment_key_fingerprint, content_policy, keys[], attestations[], turns[], skipped[], ledger{checkpoint, entries[], complete_segment, note?}, custody{signer, key_on_host, anchoring}, how_to_verify, limits}` (`limits` states what the custody allows — a host-held key could re-sign history); each attestation `{kind, artifact_id, label, source:"as_signed_when_answered"\|"current", history_id, version_hash, signed, signature, signer_fingerprint, signer_public_key, payload, content{included, data?, bytes, reason?}}`; each turn `{session_id, turn, bom, provenance_chain, provenance_meta, receipt?}`. `preset:"audit"` exports every signature the caller may see and embeds `audit_pack` + `audit_pack_attestation`. Only the caller's own answers and visible artefacts are exported; anything else is listed in `skipped` with the reason. Content up to `EVIDENCE_CONTENT_LIMIT_BYTES` (default 256 KB) | any (own) |
| `GET` | `/v1/evidence/artifact/<kind>/<artifact_id>/usage` | **Which answers used this artefact**: `{answers, since_current, current_hash, turns[]}` — `since_current` counts the answers that used the version it has now. The caller's own answers; `?scope=system` counts every account's (**403** without `VIEW_ALL_SESSIONS`). `?limit=` (≤500) | any (own); `VIEW_ALL_SESSIONS` for `scope=system` |

> **Evidence records outlive the session (PROP-009 §9.3).** Each turn with a bill of materials writes one content-free record (session, turn, profile, chain tip, key fingerprint, the bill of materials — hashes and attestation references, never answer text) and one usage row per part, at the persistence chokepoint. A deleted session's evidence stays readable. These endpoints exist only while the Ontology Suite is present.

> **Owner-scoped agent attestations (PROP-009 §4.1).** The bootstrap agents share their profile ids across accounts, so the portable agent signature (`agent`) and the deployment attestation (`agent_deployment`) are stored per **(kind, artifact_id, owner)**. One account signing, re-signing, revoking or deleting its agent never touches another account's. Every other kind has platform-unique ids (or is platform-wide by design) and is unaffected. Existing rows were attributed once at startup: to the sole account holding the agent, else to the signer if the signer holds it; anything else stays unassigned and appears in `GET /v1/trust/unassigned`.

**Ingredients:** an agent composes over its active ontology, its active skills, **and the knowledge/planner repositories it consumes** — so for a Focus agent the gate runs two levels deep (agent blocked by corpus, corpus blocked by its own screening). Applicability mirrors the runtime **per class**: a `rag_focused` agent's corpus always counts (Focus retrieval is unconditional), any other class counts a knowledge repo only when `knowledgeConfig.enabled` is truthy (runtime default false); a planner repo counts for planning classes (`tool_enabled`/`genie`). See [TRUST_MEMBRANE_ARCHITECTURE.md §3a](../Architecture/TRUST_MEMBRANE_ARCHITECTURE.md).

**States:** ⚪ `unsigned` (not validated) · 🟢 `trusted` (signed + intact) · 🟠 `changed` (signed, but an ingredient/composition changed since — tamper-evident) · ◒ `deployment_unattested` (publisher-signed and intact, but this instance's model/tools/connectors are not yet attested).

**Two halves.** `trust.portable_state` is the artefact's own state (travels with a pack and reinstates on install); `trust.deployment` is `{state, required, drift[]}` for the resolved capability surface *here*, which never travels. 🟢 requires both. `drift[]` names what changed ("tools appeared: base_writeQuery(sql)") so re-attesting is an informed act.

**Governance:** signer tier ∈ `approver_tiers` (default `admin`) + separation of duties (no self-sign under an approval policy) — reuses the Tier 4.3 ontology-governance policy. **Ownership:** every owned-kind sign-off (ontology, knowledge, planner, flow, dashboard, canvas, agent-pack) additionally requires the caller to BE the artefact's owner — **403** otherwise, and the check **fails closed** (a lookup error or a NULL-owner row denies); `skill`/`extension` are platform-level (no per-user owner) and stay tier-gated only. Signing reuses the Ed25519 provenance key; a row recorded while the provenance key was unavailable (`signed=0`) is **never** treated as an attestation — it does not turn a badge green. Badge resolution fails closed too: an attested artefact whose live hash could not be computed reads 🟠 `changed`, never 🟢. With no attestations every agent reads ⚪ (byte-identical baseline). Every successful sign/attest broadcasts a live `trust_event_notice` so open views refresh in place.

**Example — full flow:**
```bash
JWT=... ; PID=profile-...
# baseline badge
curl -s -H "Authorization: Bearer $JWT" .../v1/trust/agent/$PID          # → state: unsigned, blocking: [skill:...]
# sign ingredients then the agent
curl -s -X POST -H "Authorization: Bearer $JWT" .../v1/trust/skill/<sid>/sign-off
curl -s -X POST -H "Authorization: Bearer $JWT" .../v1/trust/agent/$PID/sign-off   # → state: trusted
# verify
curl -s -X POST -H "Authorization: Bearer $JWT" -d '{"kind":"agent","artifact_id":"'$PID'"}' .../v1/trust/verify   # → verified: true
```

---

### 3.39a. Assurance Bindings & the Module Plane (PROP-003)

**Which module assesses which property, in which lane** — the binding table, served and
persisted — plus the third-party module plane that can contribute new ones. Architecture:
[ASSURANCE_MODULE_PLANE_ARCHITECTURE.md](../Architecture/ASSURANCE_MODULE_PLANE_ARCHITECTURE.md).
Design + rejected alternatives: PROP-003 (`docs/proposals/PROP-003-assurance-module-plane.md`).

| Method | Path | Description | Tier |
|---|---|---|---|
| `GET` | `/v1/assurance/bindings` | The whole table — **including unbound rows**, because "nothing assesses this" is the reading that must not be silent. Returns `rows[]` (property · lane · meaningful · assessor · bound · note), `symmetry{}` per property, `inflight_consumers{}` (which properties a gate really resolves in flight), `overrides[]`, `modules[]` (each with `builtin` + `trust`), `drift[]` (bound modules that are no longer valid), `mechanisms[]` (the PLATFORM's own machinery per station — without it every guarantee reads "nothing acts here" on a fresh install), `controls[]` (third-party controls with their effective `posture`, whether it was `configured`, and `acts_now`), and `control_acts`/`control_postures` (the closed vocabularies). Each `rows[]` entry also carries `station`, derived from the one declaration of mechanism ownership | any (reads what the vocabulary endpoint already discloses) |
| `GET` | `/v1/assurance/bindings/candidates?property=&lane=` | Which modules COULD serve this property in this lane — **and why the others cannot**: every candidate carries `eligible` + `blocked_by[]` verbatim from `conformance`. **400** for an unknown property or lane | any |
| `POST` | `/v1/assurance/bindings` | Bind a module. Body `{property, lane, assessor, note?}`. **Fails closed**: conformance decides (via `registry.bind`, which re-runs every rule including the third-party trust gate) BEFORE anything is persisted → **409** with `reasons[]`. Audited as a receipt + broadcast live | **admin** |
| `DELETE` | `/v1/assurance/bindings/<property>/<lane>?restore_default=` | Two DISTINCT acts: `true` drops the override so the **shipped default** applies again; `false` persists an **explicit unbind** (the lane reads NOT ASSESSED). Collapsing them would make one of the two intentions unreachable | **admin** |
| `POST` | `/v1/assurance/controls/<key>/posture` | Set a control's posture — the one act that lets third-party machinery act at all. Body `{posture, note?}` from `off · observe · annotate · enforce · review`. **Fails closed** on an unrecognised value (**400**) rather than persisting something `effective_act` would later resolve as `observe`, so the stored state always means what it says. **404** for an unknown control. Returns `acts_now` and an `effect` sentence describing what CHANGED, not the value that was picked | **admin** |
| `DELETE` | `/v1/assurance/controls/<key>/posture` | Forget the decision — the control returns to DORMANT. **Distinct from setting `observe`**: "nobody has decided" and "somebody decided the default" are different facts, and only the first should leave the deployment attestation | **admin** |
| `GET` | `/v1/assurance/modules` | Installed packs with their contributions, problems and trust state, plus the packs that were **not** loaded and why, plus the plane's settings | any |
| `POST` | `/v1/assurance/modules/settings` | The plane's governance switches (`third_party_assurance_enabled`, `assurance_module_marketplace_enabled`, `disabled_assurance_modules[]`). Re-discovers immediately — a governance switch that needs a restart is one an operator distrusts | **admin** |
| `POST` | `/v1/trust/assurance-module/<pack_id>/sign-off` | Validate and sign a pack. The validator checks **capability honesty** as well as code safety: a pack claiming the verified (pure) tier while failing the subset is **blocked**, naming the lines. **422** with the verdict when not signable | signer (admin) |
| `POST` | `/v1/assurance-modules/<pack_id>/publish` | Publish an installed **user** pack (`visibility ∈ public\|targeted`). Carries the publisher's trust envelope. **409** if already published, **404** for a pack that is not installed | any (own pack) |
| `GET` | `/v1/marketplace/assurance-modules` | Browse public listings + those shared with you. Each row carries its tiers, contribution counts and `publisher_signed` | any |
| `POST` | `/v1/marketplace/assurance-modules/<id>/install` | Materialize the pack. Re-checks the blob hash (**409** on mismatch, nothing written) and reinstates the publisher's signature only if the copy is byte-identical | any |
| `POST` | `/v1/marketplace/assurance-modules/<id>/rate` | Rate 1–5. **403** unless you actually installed it | any |
| `DELETE` | `/v1/marketplace/assurance-modules/<id>` | Unpublish (publisher only) | owner |

**Installing binds NOTHING**, and a marketplace install grants **availability, never
authority**: the pack still cannot be bound until it is signed on *this* deployment. A
publisher's signature says "this is what I published", not "your deployment trusts this
inside its answer path". The install response says so in words.

**The master switch ships OFF** (`third_party_assurance_enabled`), so a deployment that
does nothing runs byte-identically to before: no discovery, no registration. The
marketplace can never be more permissive than the plane it feeds — both switches must be on.

**Drift never auto-unbinds.** A bound module whose content hash has moved keeps running
and is reported in `drift[]` (and as a danger-severity inbox item); removing a running
protection without a decision is worse than running a changed one visibly. The binding
table also folds into the **deployment** half of the trust envelope, so a rebind flips that
face until re-attested, and `drift[]` on the agent names the property and the module.

---

### 3.40. Governed Metric Semantic API for BI (Tier 4.2 / T3)

A metric is **defined once** in the ontology (a KG `metric` node) and **consumed everywhere** —
by the agent (`compile_metric` tool) and by BI tools (these endpoints) — through one pure
semantic core, so both faces produce byte-identical SQL. All endpoints are tier-gated (KG
feature) and ownership-scoped to the profile's *active* ontology (`?kg_id=` targets a specific
one). See `TIER_4.2_METRIC_API.md`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/profiles/<id>/metrics` | Metric **catalog** — summary rows (name, description, dim/measure counts, realization, queryable, access scope) + the ontology's governance/trust. |
| `GET` | `/v1/profiles/<id>/metrics/<name>` | Full metric — `definition` + `compiled` SQL + `deployment` pointer + `access_scope` + `ontology` (lifecycle/trust) + `standards_export` (RDF Data Cube link). `?include_conformance=true` adds conformance. |
| `POST` | `/v1/profiles/<id>/metrics/<name>/compile` | Compile a **slice**. Body `{dimensions?, measures?}` (omit = all; `[]` = none). Returns governed SQL + selection + access scope. |
| `POST` | `/v1/profiles/<id>/metrics/<name>/query` | Compile **and execute** against a DB connection → `{columns, rows, row_count, …}`. Body `{dimensions?, measures?, connection_id?, max_rows?}`. Read-only (single SELECT only). `409` (with the SQL) if no connection. |

```bash
JWT=$(curl -s -X POST .../v1/auth/login -d '{"username":"admin","password":"admin"}' | jq -r .token)
PID=profile-...   # a profile with an active ontology that has metrics

# discover
curl -s -H "Authorization: Bearer $JWT" ".../v1/profiles/$PID/metrics" | jq '.metrics[].name'
# inspect (definition + compiled SQL + governance/trust)
curl -s -H "Authorization: Bearer $JWT" ".../v1/profiles/$PID/metrics/Total%20Revenue" | jq '{ontology, compiled, access_scope}'
# compile a slice
curl -s -X POST -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' \
     -d '{"dimensions":["order_date"],"measures":["revenue"]}' \
     ".../v1/profiles/$PID/metrics/Revenue/compile" | jq .sql
# query (rows)
curl -s -X POST -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' \
     -d '{"connection_id":"<id>","max_rows":100}' ".../v1/profiles/$PID/metrics/Revenue/query" | jq '{columns, row_count}'
```

---

### 3.41. The Vote Signal (per-turn feedback)

A user's per-turn upvote/downvote reaches every engine's own learning mechanism and the
evaluation layer. The **write** endpoint is unchanged and is the platform's single entry
point; what changed is what happens behind it. See
[FEEDBACK_SIGNAL_ARCHITECTURE.md](../Architecture/FEEDBACK_SIGNAL_ARCHITECTURE.md).

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/session/<session_id>/turn/<turn_id>/feedback` | Cast, flip or clear a vote. Body `{"vote": "up" \| "down" \| null}`. Persists `turn["feedback"]`, then **fire-and-forget** dispatches to every consumer — the response never waits on one, and a consumer failure can never fail the vote. |
| `GET` | `/v1/knowledge/repositories/<collection_id>/retrieval-feedback` | Per-document ratings for a knowledge repository — the Corpus Health curation signal. Owner-scoped (404 unknown, 403 not yours). |

**`retrieval-feedback` response:**

```json
{
  "collection_id": 12,
  "rated_documents": 7,
  "lowest_rated": [
    { "document_id": "…", "name": "policy_v3.pdf", "up": 0, "down": 3, "net": -3 }
  ]
}
```

Only documents with a **negative net** appear — a document nobody complained about is
not a finding. Counts resolve the *latest* vote per `(session, turn, document)`, so a
user who changes their mind is counted once, in their current position. **Records only:**
nothing here reranks, scores or quarantines.

**Vote fields on the existing agent views** (both optional, and **absent** unless the
caller has votes — never a zero, which would read as "nobody rated this"; omitted
entirely in `scope=system`, where a vote belongs to another user):

| Endpoint | Added |
|---|---|
| `GET /v1/evals/online/dashboard` | per-agent cell: `upvotes`, `downvotes`, `vote_disagreements` |
| `GET /v1/evals/agents/<tag>/runs` | per-run row: `"vote": "up" \| "down"` |

A vote is a **preference, not a measurement**: none of these fields is folded into
`pass_rate`, `failures`, `series` or a band (Rule 3bb.4). `vote_disagreements` counts
turns where the scorers and the user point in opposite directions — the calibration
signal behind Config → Trust → Quality bands.

**Governance** — `APP_CONFIG.FEEDBACK_SIGNALS`: `enabled` (master), `annotate_eval`,
`champion_upvote`, `genie_propagation`, `retrieval_feedback`, `memory_deactivate` all
default **on** and are deterministic + 0 tokens; `judge_on_downvote` defaults **off** and
is the only one that spends tokens.

---


### 3.42. OBDA-READ Data Products (Tier 5.5)

The surface for **signed data products** — producer-declared, machine-validated,
Ed25519-signed consumption contracts over an ontology: same request → same governed
SQL → same rows. Full architecture:
[OBDA_ARCHITECTURE.md](../Architecture/OBDA_ARCHITECTURE.md).

**Availability:** every endpoint in this family is gated on the OBDA-READ **component**
(`obda_read` — Administration → Components for the deployment, the caller's per-user
override; there is no master switch since Oct 2026). Not available ⇒
`403 {"error": "obda_read_off"}` with a message naming where to enable it. All endpoints are additionally KG-tier-gated and
ontology-ownership-scoped (`kg_id` resolves through the caller's own graphs).

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/data-products?kg_id=<kg>` | The catalog: every product on the ontology with `{product, version, backend, description, validated, signable, trust: {state, attested_hash, live_hash}}`. |
| `POST` | `/v1/data-products` | Author (create or edit). Body `{kg_id, manifest}`. A manifest violating an M-rule is a `400` carrying its machine-readable `error` code + a near-miss hint. **Editing a manifest explicitly drops the stored validation report** (the ratchet: edit ⇒ re-validate ⇒ re-sign). Composed manifests (`members` + `bridges`) are accepted; the flat form must parse and every member must exist. |
| `GET` | `/v1/data-products/<name>?kg_id=<kg>` | The definition: manifest, stored validation report, trust state, and the **admissible map** (measures · dimensions · the grain-safety matrix · filter ops · time grains) — published *before* asking, so a query-time `grain_conflict` can only mean the consumer ignored it. |
| `DELETE` | `/v1/data-products/<name>?kg_id=<kg>` | Delete the product node (member edges cascade) **and revoke its attestation** — a dangling signature over a deleted contract attests nothing. The ontology's own seal is untouched by design. Second delete ⇒ honest `404`. |
| `POST` | `/v1/data-products/<name>/validate` | Run the sign-time gate **V1–V7** and stamp the report onto the node. Body `{kg_id}` + optional `connection_id` (or `live: true` for the default connection) to run **V7 live probes** at the real target — battery executes with limit 1, declared cardinalities and entity keys spot-checked empirically. No usable connection with live requested ⇒ the gate runs structurally and the response carries an honest `live_note`. The report's `mode` (`"live"` \| `"structural"`) is **attested content** — it changes the report hash. The response also carries **`trust`** — the state the validation left behind (`{state, attested_hash, live_hash, signed_at}`): validation NEVER signs, but content that returns byte-identical to a signed contract matches its EXISTING attestation again and reads `trusted` with no act by the operator; without this field the client cannot distinguish a fresh signature from a re-affirmed one (and a live walkthrough read exactly that as "validate auto-signed it"). |
| `POST` | `/v1/data-products/<name>/compile` | Compile a §3 request → governed SQL (never executes). Body `{kg_id, request}`. A REJECT is `422` with its closed-set `error` code and the closest declared names. |
| `POST` | `/v1/data-products/<name>/query` | Compile **and execute** read-only under the caller's own DB connection (`connection_id` optional — default connection used; none ⇒ honest `409` *with* the compiled SQL). Response carries rows + the **governed provenance stamp** `{product, version, request_hash, trust, lane: "governed", row_count}`. |
| `POST` | `/v1/trust/data-product/<kg_id>/<name>/sign-off` | The **earned signature**: refuses `invalid_manifest` / `unvalidated` / `stale_report` / `gate_blocked` / `unsigned_ingredient` (the ontology) / `unsigned_member` (a composed product's member, P2 — named) as `422`/`409`; success signs `{manifest_hash, validation_report_hash}` (+ `member_hashes` lineage for composed products). Signer-tier + ontology-ownership gated. |

**The request contract** (`request` body for compile/query): `measures` +
`dimensions` (as `entity.attribute`, role-qualified `entity@role.attribute` where
parallel joins exist), structured `filters` `[{attr, op, value}]` from a **closed
operator set** (never SQL), optional `time_grain`, `order_by`, `limit` — or
`entity` + `attributes` for a plain read. Consumption is deterministic:
`request_hash` identifies the exact slice.

**Composed products:** a manifest with `members` (≥ 2 signed base products on the
same ontology) + `bridges` flattens through the unchanged pipeline at every
endpoint above. A parent's re-sign flips the composed child's trust to `changed`
(stale-ingredient, P3); a parent *shape* change additionally forces re-validation
(`stale_report`).

**MCP face:** the same capability at user tier on the Uderia MCP server —
`list_data_products(kg_id)` / `query_data_product(kg_id, product, request)`
(§3.33); REJECTs and the disabled switch return as structured error dicts.

### 3.42a. Signed Actions (PROP-008 OBDA-ACT)

An **action** is a signed WRITE contract on an ontology: the target entity and its deployed
table, the key, an allowlist of writable attributes with types and value domains, pre/post
conditions, a row bound and a posture (`observe` · `review` · `annotate` · `enforce`). It is the
only way an agent writes. Every face — the agent's `TDA_Action`, this REST face and the MCP tools —
calls ONE seam (`core/action_types` to author and validate, `core/action_perform` to preview,
perform and undo), so authority, posture, the transaction and the receipt cannot differ by caller.
All endpoints are gated on the OBDA-ACT **component** (`action` — Administration → Components;
not available ⇒ `403 obda_act_off`) and the knowledge-graph tier gate. A graph that is not the caller's is `404` (not found and not yours are
one answer).

**Who acts.** Most endpoints take `kg_id` and/or `profile_id`. With `profile_id` the caller acts
**as that agent** — its `authorityConfig.actions.types` is the grant (`403 not_granted` names the
grant), and without `kg_id` the agent's active graph is used. Without `profile_id` the caller acts
with their own authority over their own graph. **The posture holds for every face:** a
review-posture action parks for its owner (`202`), an observe-posture action only records.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/actions?kg_id=<kg>` \| `?profile_id=<p>` | The catalog: `{action, version, operation, entity, target, key, writable, domains, posture, max_rows, validated, signable, trust}` per action; with `profile_id` each row also carries `granted`. |
| `POST` | `/v1/actions` | Author (create `201` / update `200`). Body `{kg_id, manifest}`. A manifest violating a rule is a `400` carrying its closed `error` code. **An edited contract drops its stored validation report** (`manifest_changed: true`) — re-validate before signing. A draft authored target is refused (`draft_target`). |
| `GET` | `/v1/actions/<name>?kg_id=<kg>` | The definition: `summary`, `manifest`, `manifest_hash`, `validation_report`, `ledger` (the platform ledger table, e.g. `fitness_db.UDERIA_ACT_Customers`, or `null` when the target keeps its own audit columns) and `trust`. |
| `DELETE` | `/v1/actions/<name>?kg_id=<kg>` | Delete the action node **and revoke its attestation**. The ontology's seal is untouched (action nodes are outside its hash); the ledger table in the warehouse is kept — it is an audit record. |
| `POST` | `/v1/actions/<name>/validate` | Run **A1–A6** and stamp the report. Body `{kg_id, connection_id?}`: with a Teradata connection the gate runs **live** (dictionary, unique keys, the ledger provisioned, SELECT/EXPLAIN probes); without one it runs structurally and `mode: "structural"` says so. Validation never signs. |
| `POST` | `/v1/actions/<name>/preview` | **The consequence before anything happens.** Body `{kg_id \| profile_id, key, values, connection_id?}` (or `invocation: {key, values}`). Every refusal a perform would make, then the write executed inside a transaction that is **always rolled back** — so `consequence.lines` (`column before → after` per row), `consequence.rows` against the bound and every check are the database's answer, never a prediction. Returns `invocation_hash` + `statement_hash`, plus `pending` (the block a Confirm posts back to `…/perform`) and `row_html` (THE prepared-act row the agent's answer draws — one builder); `written: false`. Optional `face: "dashboard"` labels the block with the surface it is confirmed on (a closed set — any other value is dropped); `…/perform` signs that face into the receipt's occurrence and stamps it on the record (else `rest`). A compile refusal is `422 {status: "refused", refused_check, refusal}` naming the fix (e.g. the domain). An update whose keyed row already holds every supplied value is refused the same way with `refused_check: "no_change"` — there is nothing to confirm; a review posture likewise parks nothing (`…/perform` → `422 refused`, `no_change`). |
| `POST` | `/v1/actions/<name>/perform` | Perform. Body as preview, plus optional `invocation_hash`, `session_id`, `turn_id`. **With `invocation_hash` it performs only the invocation that was previewed** — a different value set is refused (`422`, `refused_check: parked_drifted`), nothing written, and the record names `confirmed_by`. This body is exactly the `pending` block the agent's consequence row carries. Responses: `200 performed` (the record carries a **`signed_receipt`** — Ed25519 over `payload` = `{receipt: <the sealed receipt fields, also as content>, occurrence: {face: "rest", ref, session_id, confirmed_by, at}}`, verifiable offline; a REST act happens outside any sealed turn so its receipt is signed on its own and names when and where it happened) · `202 parked` (review posture; `record.review_id`) · `200 observed` · `422 refused`. `record.face` names the door: `"conversation"` when the confirmation is claimed on a session + turn, the body's `face` when it is one of the closed set (`dashboard`, `mcp` — the MCP server sends `mcp`), otherwise `"rest"`. **With `session_id` + `turn_id` the confirmation is claimed on that turn first** (`turn['action_confirmations']`, atomically, before anything is written): a second confirmation of the same invocation is `409 already_confirmed` carrying the record already held; an unknown session is `404 session_not_found`; a refused act is retryable. |
| `GET` | `/v1/actions/confirmations?session_id=<s>` | What was confirmed in a conversation: `{confirmations: {"<turn>": {"<invocation_hash>": record}}}`, each record `{action, version, outcome, rows_affected, signed_receipt, review_id, refusal, confirmed_by, confirmed_at, undoes}`. A reloaded answer reconciles its rows from this, so a reload never offers a Confirm for an act already done. |
| `GET` | `/v1/actions/activity?sessions=<n>` | The caller's acts across their `n` most recent sessions (default 30, max 100), read from the turns themselves — each turn's `actions` folded with the confirmations given afterwards (`action_perform.summarize`), so a confirmed or approved act counts once, as how it ended — **plus the acts no turn holds** (a flow, a schedule, a dashboard tile, REST, MCP, an approval in the Assurance Inbox), read from the platform act log (`core/action_act_log.py`; conversation acts are not added twice): `{by_agent: {"<tag>": {performed, refused, parked, prepared, observed, turns, unattended, refusals[≤5]}}, by_action: {"<action>": {performed, refused, parked, prepared, observed}}, by_face: {"<face>": {performed, parked, observed, refused}}}`. Read by the Trust Circle's BOUNDED evidence and the Designer's *in use* step. The caller's own sessions only. |
| `GET` | `/v1/actions/<name>/grants` | Which of the caller's agents are granted the action — `{agents: [{profile_id, tag, profile_type, granted, component_on}]}`, granted first. `component_on` says whether the agent can reach `TDA_Action` at all (a grant without the component is a list nobody reads; `null` when it cannot be decided). |
| `GET` · `POST` | `/v1/actions/approvers` | **Four eyes** (PROP-008 §5.4a) — the accounts that decide one of the caller's agents' parked writes. `GET ?profile_id=` → `{approvers: [usernames], unknown: [names no longer active]}`. `POST {profile_id, approvers: [usernames]}` sets `authorityConfig.actions.approvers` (merged server-side; `[]` removes the designation — the owner decides again). **400 `approver_unknown`** for a name that is not an active account, **400 `approver_is_owner`** when the agent's owner is named (nothing saved). With a designation, a parked write is decided only by an approver; a park whose designation resolves to no active account is refused (`approver_unknown`). Access, not trust: moves no signature. |
| `POST` | `/v1/actions/<name>/grant` | Grant or withdraw the action for one of the caller's agents — `{profile_id, granted}`. Edits ONLY `authorityConfig.actions.types` (every other authority key, and every other granted action, is kept — the merge happens server-side, never through a whole-block profile update). The grant is access, not trust: it moves no signature. Returns `{types, component_on}`; `404` for an agent that is not the caller's. |
| `POST` | `/v1/actions/<name>/undo` | The invocation that undoes a performed one — the ledger's before-image of the same keyed row, written back through the SAME signed contract — **returned previewed, never performed**. Body `{kg_id \| profile_id, invocation_hash, connection_id?}`; perform it with `…/perform` like any act. An insert's undo (a delete) is refused `422 not_undoable` in v1. |
| `POST` | `/v1/trust/action/<kg_id>/<name>/sign-off` | The **earned write signature**: refuses `invalid_manifest` / `unvalidated` / `stale_report` / `gate_blocked` / `unsigned_ingredient` (the ontology is not signed-and-intact) as `422`; a live-validated action signs clean (`verified_write`), a structural one partial (`verified_declared`). Signer tier + the graph's ownership. |

**Refusals no posture can relax** (`error` codes): `act_off` `403` · `not_granted` `403` ·
`role_not_held` `403` (on Teradata the connection's login must hold the manifest's role) ·
`not_found` `404` · `not_trusted` `409` (an unsigned or changed contract is never performed) ·
`connection_unavailable` `409` (the connection is decided, never guessed: explicit → the
connection the action was validated through (it owns it) → the caller's only Teradata/SQLite
connection — otherwise refused naming the candidates; nothing on the agent decides) · `parked_drifted` `409` ·
`invalid_manifest` / `not_undoable` `422`.

**MCP face:** `list_actions(kg_id?, profile?)` and `perform_action(action, key, values,
invocation_hash?, kg_id?, profile?, connection_id?)` at **developer** tier, never user (§3.33).
`perform_action` is two-step: without `invocation_hash` it previews (nothing written) and returns
the hash; called again with it, it performs. Refusals return as structured dicts.

### 3.39b-w. Withdrawing — the inverses (Oct 2026)

What a person published, landed, parked or signed can be taken back through the same API,
live, as the account itself — so no rehearsal or clean-up ever needs a second process on
the server's database. One rule across the family: **a record of something that exists is
never deleted** — the act log has no delete face, an approved or landed tuning proposal
stays, a decided Inbox item stays, a landed warehouse table stays until a person drops it.

| Method | Path | Description |
|---|---|---|
| `DELETE` | `/v1/knowledge-graph/authored/<extension_id>?kg_id=` | **Withdraw an extension** — the inverse of publish. Every authored node of the extension and its design decisions, the physical nodes a landing wrote for them (and the landing's schema node once nothing else realises into it, with the ledger artefact under it), their retrieval telemetry, and the extension's signature go in one act. The warehouse is not touched. Agents grounded on the graph read *changed* until re-signed, as after a publish. Gated like publish: the OBDA-MODEL component (`403 obda_model_off`), the KG tier, graph ownership (`404`); `404 not_found` when the graph holds no node of that extension. Returns `removed[]`, `authored`, `physical`, `usage_rows`, `signature_revoked`. |
| `DELETE` | `/v1/knowledge-graph/tuning/proposals/<rid>?kg_id=` | Remove a tuning proposal that landed nothing (pending · failed · rejected · expired). `409 not_deletable` for an approved or landed one — the structure exists; undo it in the warehouse first. |
| `DELETE` | `/v1/knowledge-graph/versions/<version_id>?kg_id=` | Remove one snapshot of the caller's graph. |
| `DELETE` | `/v1/assurance/reviews/<item_id>` | The owner withdraws one of their own **pending** items — a parked write nobody will decide. Nothing was written for it; the act log keeps the park. `403 not_owner` for a designated approver, `409 not_pending` for a decided item. |
| `DELETE` | `/v1/trust/<kind>/<artifact_id>/signature` | The owner withdraws an artifact's attestation: it reads unsigned again; the trust history keeps the signing and the revocation; every open surface updates live. Same authority as signing it (signer tier + per-kind owner). |

### 3.39c. Workload Tuning (PROP-008 C1)

Proposals for performance structures (statistics, covering join indexes, partitions) derived from
the **recorded workload** of the agents grounded on a graph, against the graph's deployed tables.
The review only READS (dictionary queries and `EXPLAIN` of single guarded `SELECT`s); nothing
lands until a **person** approves a proposal and lands it. All routes are gated on the OBDA-MODEL **component**
(`obda_model`; not available ⇒ `403 obda_model_off`) and the knowledge-graph tier gate; a graph that is not the
caller's is `404`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/knowledge-graph/tuning/review` | Body `{kg_id, connection_id}` (a Teradata Database Connection). Returns `review` (`workload: {statements, agents}`, `explain: {explained, failed, distinct}`, `findings` — modelling gaps, and for a **view** the workload keeps reading (`kind: "view"`, `base_tables`, `columns`) a finding naming the base tables to tune instead (the warehouse keeps statistics and indexes on tables; nothing is proposed on a view), `tables` facts (each with `kind` and, for a view, `base_tables`), `unavailable` — tables whose facts could not be read, named) and the graph's `proposals` after recording. A new proposal is `pending`; a pending one has its evidence refreshed; a decided one is untouched; a pending one the workload no longer supports is `expired` (evidence kept). |
| `GET` | `/v1/knowledge-graph/tuning/proposals?kg_id=<kg>[&status=<s>]` | The graph's proposals: `{id, proposal_id, rule, purpose, table, landing, status, proposal{columns, key, evidence, rationale, ddl, measure_sql}, decided_by, decided_at, note, receipt}`. |
| `POST` | `/v1/knowledge-graph/tuning/proposals/<id>/approve` | A person approves a `pending` (or `failed`, to retry) proposal. `409 not_pending` otherwise; `409 rebuild_required` for a partition proposal (`landing: "rebuild"` — the platform never rebuilds a table). Body `{note?}`. |
| `POST` | `/v1/knowledge-graph/tuning/proposals/<id>/reject` | Body `{note?}`. |
| `POST` | `/v1/knowledge-graph/tuning/proposals/<id>/land` | Land an **approved** proposal (`409 not_approved` otherwise — nothing lands without approval). Body `{connection_id?}` (default: the review's). EXPLAINs the motivating workload statements before and after, executes the rendered statements one per round trip, and returns the proposal with its signed `receipt`: `{receipt_id, statements, landed, errors, measured_before, measured_after` (summed estimated seconds)`, unconfident_before/after` (plans that rested on a low- or no-confidence estimate)`, plans_changed` (plans whose shape changed — a cost estimate can move with nothing changed)`, approved_by, by, attestation}` and `artifact_node` (the `ddl_artifact` written to the graph). `200` landed; `422` when the database refused a statement (status `failed`, the error named). |

### 3.40a. Database Navigator

The panel face of the `database_navigator` component ([architecture](../Architecture/DATABASE_NAVIGATOR_ARCHITECTURE.md)). Every endpoint is gated on the component being available to the caller (admin + per-user governance, failing closed) and answers only from the caller's own Database Connections or the caller's active MCP session; all reads route through the same `NavigatorService` the `TDA_DatabaseNavigator` tool uses. Source ids are `conn:<connection id>` (direct) or `mcp:<server id>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/database-navigator/sources?profile_id=` | The sources this agent can read (`sources[]` with `kind`, `driver`, `capabilities[]`), the resolved `default` and the `reason` it was chosen |
| GET | `/v1/database-navigator/databases?source=&profile_id=&refresh=` | Databases with `object_count`, `size_bytes`, `is_system`; `summary.system` counts the flagged ones |
| GET | `/v1/database-navigator/objects?source=&database=&refresh=` | Objects (`kind`, `row_count` + `row_count_source` = `statistics`/`exact`, `size_bytes`, `column_count`) + a deterministic `summary` |
| GET | `/v1/database-navigator/object?source=&database=&name=&profile_id=&refresh=` | One object: `columns[]` (type, nullable, `is_primary`, `flags[]`, `stats`, `ontology`), `foreign_keys_out/in[]` (`declared` true), `indexes[]`, `view_text`, `intelligence` (role + reasons, keys, `inferred_joins[]`, `scope`, actions, questions, `sample_sql`, source capabilities), `ontology`, `unavailable[]` |
| GET | `/v1/database-navigator/search?source=&q=&database=&system=` | Ranked `hits[]` (`kind` database/object/view/column, `score`, `why`); `scope` = `dictionary` (one dictionary read) or `cached`; system databases excluded unless `system=1` |
| POST | `/v1/database-navigator/preview` `{source, database, name, limit≤500, unmask, profile_id}` | Sample rows; columns flagged `pii` (dictionary name rules or the ontology) come back masked (`masked_columns[]`) unless `unmask` is explicitly true |
| POST | `/v1/database-navigator/count` `{source, database, name}` | Exact `COUNT(*)` (the cached detail switches to `row_count_source: exact`) |
| POST | `/v1/database-navigator/refresh` `{source?}` | Drops the caller's cached catalog (all sources or one) |

Errors: `403 component_disabled`, `400/404 catalog` with a recovery-shaped `message` (e.g. *connection 'x' not found — pick another source*, *refusing to run a non-SELECT statement*).

A **MongoDB** source answers the same routes: collections are objects (`kind` `table`, a view
`view`), their fields are **sampled** from up to 200 documents (each `columns[].data_type` names
every BSON type seen, `stats` carries `sampled_documents` / `present_in` / `presence`, and
`unavailable[]` says *a declared schema — fields sampled from N documents …*); `sample_sql` and a
preview's `sql` are JSON statements, not SQL.

### 3.43. Schema Ledger (admin)

`GET /api/v1/admin/schema/ledger` — **admin only**, read-only. What each start did to the
authentication database (`tda_auth.db`), the upgrade backups on disk, and the schema drift.

Query: `limit` (default 20) — number of ledger rows.

```json
{
  "status": "success",
  "backup_mode": "required",
  "records": [
    {"id": 3, "started_at": "2026-10-02T09:14:02+00:00", "app_version": "v1.24-29-g1a2b3c4",
     "fingerprint": "…", "previous_fingerprint": "…", "upgrade": 1,
     "backup_path": "/app/tda_auth.db.bak-20261002-091358-123456-upgrade", "backup_note": null,
     "schema_before": "…", "schema_after": "…", "changed_without_backup": 0,
     "drift": {"missing_tables": [], "missing_columns": {}, "unchecked_files": []}}
  ],
  "backups": [{"path": "/app/tda_auth.db.bak-20261002-091358-123456-upgrade", "bytes": 1073741824}],
  "drift": {"missing_tables": ["installed_assurance_modules"],
            "missing_columns": {"kg_profile_assignments": ["created_at"]},
            "unchecked_files": ["25_hybrid_search.sql"]}
}
```

- `upgrade` — the start's fingerprint (schema files, DDL lines of the sources, ORM DDL, release
  version) differed from the previous start's on a database that already held tables; a backup
  was taken before the bootstrap ran (`VACUUM INTO`, integrity-checked, the newest 3 kept).
- `changed_without_backup` — the database schema changed during a start whose fingerprint did
  not: the change came from code the fingerprint does not read.
- `drift` — tables and columns the `schema/*.sql` files declare that the database lacks;
  `unchecked_files` could not be evaluated on their own (they alter tables only the ORM creates).
  A report, never a repair.

`TDA_SCHEMA_BACKUP` (`required` default · `best_effort` · `off`): under `required` a start whose
backup cannot be taken is refused before the database is changed.

---

### 3.44. Files and File Stores (PROP-010)

Everything a session produces — kept tables, charts and answers, every chat attachment — lives in an
admin-configured, encrypted **file store**. A file is owned by the user, placed by the agent of the turn
that made it, and traced to that turn. **Download is the one audited exit.** Contract:
[FILES_ARCHITECTURE.md](../Architecture/FILES_ARCHITECTURE.md). Implementation: `api/files_routes.py`.

All endpoints require auth (`/api` prefix). User endpoints act on the caller's own files only. Writes
that create a file (`POST /v1/files`, `/keep`, `/<id>/keep`, `/dashboard-snapshot`, `/save`) answer
`403 component_disabled` when the Files component is turned off for the caller (Administration →
Components). Errors share one shape: `{"status": "error", "message": "...", "code": "<code>", ...}`.

#### 3.44.1. User endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/files` | List / search. Query: `q`, `kind` (`all` · a category · `deleted`), `origin`, `profile_id`, `session_id`, `scheduled_task_id`, `flow_id`, `component_id`, `since`, `new=1`, `sort` (`newest` default), `limit` (200), `offset`. Returns the page plus facet counts, storage use and `available` (component on for the caller) |
| POST | `/v1/files` | **+ Add** — multipart `files` (≤ 20 per call), optional `profile_id`. Response `{files: [...], refused: [{filename, reason}]}`; `400` when nothing was added |
| GET | `/v1/files/<id>` | Metadata + `provenance` (the turn, agent, component that produced it) |
| PATCH | `/v1/files/<id>` | Rename. Body `{"filename": "..."}` |
| DELETE | `/v1/files/<id>` | Move to *Recently deleted*. Response `{purged, purge_after}` |
| POST | `/v1/files/<id>/restore` | Back out of *Recently deleted* |
| POST | `/v1/files/<id>/keep` | Promote an attachment to a kept file (it then outlives its conversation) |
| POST | `/v1/files/<id>/seen` | Clear the "New" marker |
| GET | `/v1/files/<id>/preview` | A safe, bounded representation for the viewer |
| GET | `/v1/files/<id>/download` | **The governed exit.** `?format=` converts on the way out (`original`; `csv`/`xlsx` for CSV/TSV; `docx`/`pdf` for Markdown/text); `?with_evidence=1` adds the evidence bundle. Served with `Content-Disposition: attachment`, `nosniff`, `no-store` and a sandbox CSP; audited under the tier's download policy |
| POST | `/v1/files/download` | Zip of several. Body `{"ids": [...]}`; each file is authorized and audited on its own; header `X-Files-Skipped` counts the refused ones; `403` when all were refused |
| POST | `/v1/files/keep` | Keep part of an answer. Body: `session_id`, `turn_id`, `kind` (`answer` · `table` · `chart` · `component`), optional `anchor`, `headers`, `title`, `filename`, `media_type`, `data_base64` (client-rendered bytes, e.g. a chart PNG), `allow_duplicate` |
| GET | `/v1/files/kept?session_id=&turn_id=` | The anchors already kept in a turn (drives the *Kept · Open* state) |
| GET | `/v1/files/attachments?session_id=` | Each attachment of a conversation now: `live` (with lifetime) · `deleted` (restorable until `purge_after`) · `gone`, keyed by the id its chip carries; `manifest: false` when the conversation's attachment list is unreadable |
| POST | `/v1/files/<id>/attach` | Attach a kept file to a conversation **by reference** (no copy). Body `{"session_id": "..."}`. Returns the same record shape as `POST /v1/chat/upload`; `400 not_attachable` for a type a turn cannot read |
| POST | `/v1/files/<id>/to-canvas` | Text of a code/text file as `{title, language, content}` for a new canvas; `400 not_text` otherwise |
| GET | `/v1/files/knowledge-targets` | The caller's own knowledge repositories a document can be added to |
| POST | `/v1/files/<id>/to-knowledge` | Add a kept document to a knowledge repository through **that repository's own upload flow** (owner check, ingest firewall, chunking, embedding). Body `{"collection_id": N}`; `knowledge_refused` carries the repository's refusal |
| POST | `/v1/files/save` | *Save to Files* for a browser-built export (receipt, evidence bundle, chart PNG, canvas file). Multipart `file` + optional `filename`, `component_id`, `instance_id`, `session_id`, `turn_id`, `profile_id`, `source` (JSON), `allow_duplicate` |
| POST | `/v1/files/exports/recorded` | Record a browser-built download in the audit trail (its bytes never reached the server). Body `{filename, size, what, session_id}` |
| POST | `/v1/files/dashboard-snapshot` | A dashboard as one server-written PDF, kept in Files. Body `{dashboard_id, images: {tile_id: "data:image/png;base64,..."}}` — a tile without a picture is written from its data |
| GET | `/v1/file-stores/available` | The stores the caller's agents may be bound to (`fileStoreConfigId` on the profile): `{default_store_id, stores: [{id, name, backend_type, encrypt, is_default}]}` |

**Server-built exports.** Every export the server builds (e.g. a dashboard, canvas or evidence export)
leaves through one exit: by default it downloads under the tier's download policy and is recorded;
with `?to=files` it is saved to Files instead (`{"status": "success", "file": {...}}`).

#### 3.44.2. Admin endpoints (Administration → Components → Files)

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/v1/file-stores/configurations` | List stores (with file count, bytes, bound agents, backend label, location) / create one (`201`). Body: `name`, `backend_type` (`local` · `s3` · `azure_blob` · `gcs`), `config` (backend fields; secrets are masked on read), `encrypt` |
| GET/PUT/DELETE | `/v1/file-stores/configurations/<id>` | Read / update / delete. The platform local store cannot be deleted; a store that still holds files answers `409 store_not_empty` with `files` |
| POST | `/v1/file-stores/configurations/<id>/test` | Live connection test (`{success, message}`). Id `_draft` tests an unsaved form: body `{backend_type, config, existing_id?}` (masked secrets are filled from `existing_id`) |
| POST | `/v1/file-stores/configurations/<id>/default` | Make it the default store |
| GET | `/v1/file-stores/backends` | The backend catalog (fields, labels, which are secret) |
| GET/PUT | `/v1/file-stores/settings` | The Files policy: `files_enabled`, `require_encryption`, `retention_days`, `max_file_mb`, per-tier `quota_mb` / `allowed_stores` / `download_policy`, `capacity_warn_percent`, `platform_local_capacity_mb`. Encryption cannot be turned off for a store while the policy (or hardened mode) requires it |
| POST | `/v1/file-stores/migrate` | Move files between stores. Body `{from, to, dry_run}` — `dry_run` defaults to `true` and returns a `plan` |
| GET | `/v1/file-stores/capacity` | Provisioned · held · free · promised, top users, per-user overrides |
| PUT/DELETE | `/v1/file-stores/user-quotas/<user_uuid>` | Set (`{"quota_mb": N}`) / clear a per-user quota override |
| GET | `/v1/file-stores/keys` | Which key manager wraps new files' keys, plus key usage |
| POST | `/v1/file-stores/keys/test` | Live wrap/unwrap test. Body `{provider, config}`; `provider` ∈ `platform` · `aws_kms` · `azure_key_vault` · `gcp_kms` · `vault_transit` |
| PUT | `/v1/file-stores/keys` | Switch the key manager for NEW files — saved only after the same live test passes (`400 test_failed` otherwise) |
| POST | `/v1/file-stores/keys/rewrap` | Re-wrap existing file keys under the active key manager. Body `{"dry_run": true}` (default) |

Every admin write is recorded in the audit log (`file_store_*`, `file_keys_provider`).

---

### 3.45. Agent Memory

Long-term memory the agent recalls about the user (`[Remembered about this user]`), managed by the
builtin `memory` component. Items are **agent-scoped** (one profile) or **global** (shared across all of
the user's agents). All endpoints act on the caller's own memory and require auth. Implementation:
`rest_routes.py` (`memory_*`), `components/builtin/memory/`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/memory` | List items. Query `profile_id`, `kind`. Response `{items: [...], count}` |
| POST | `/v1/memory` | Add an item (`source: "manual"`, `201`). Body: `content` (required), `kind` (`preference` · `fact` · `style`; inferred when omitted), `scope` (`agent` default · `global`), `profile_id` (no `profile_id` = global), `mem_key`. `403 component_not_available` when the memory component is off for the caller |
| PUT | `/v1/memory/<item_id>` | Edit `content`, `kind`, `active`, `confidence`, `profile_id`, `mem_key` |
| DELETE | `/v1/memory/<item_id>` | Delete one item (`{deleted}`; `404` if not found) |
| DELETE | `/v1/memory/all` | Erase ALL of the caller's memory, or one agent's with `?profile_id=` (`{erased: N}`) |
| GET | `/v1/memory/buckets?profile_id=` | The two Workspace sections: Local (this agent) and Global |
| GET | `/v1/memory/owners?active_profile_id=` | Global + every agent that has memory or the component enabled, with item counts and IFOC type |
| POST | `/v1/memory/recall-preview` | What WOULD be recalled for `{query, profile_id}` — deterministic, 0 tokens. Response `{items, count, block}` |

---

### 3.46. Marketplace — Collections, Extensions, Component Instances, Sharing

Agent packs (§3.14), knowledge graphs (§3.28) and assurance modules (§3.39a) have their own
marketplace sections. The remaining marketplace families follow the same pattern: **publish** from
the owner's resource, **browse**, then **subscribe** (live, read-only reference) or **fork/install** (an
independent copy), **rate**, and **targeted users** for `visibility: "targeted"`.
All require auth. See [MARKETPLACE_ARCHITECTURE.md](../Architecture/MARKETPLACE_ARCHITECTURE.md).

**Collections (planner and knowledge repositories)**

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/rag/collections/<id>/publish` | Publish (owner). Body `{visibility, user_ids?, marketplace_metadata?}` |
| POST | `/v1/rag/collections/<id>/unpublish` | Remove from browse; existing subscribers keep read-only access |
| GET | `/v1/marketplace/collections` | Browse. Query `search`, `repository_type`, `visibility`, `sort_by`, `limit`, `offset` |
| POST | `/v1/marketplace/collections/<id>/subscribe` | Subscribe (reference, no copy; `201`, `409` if already subscribed) |
| POST | `/v1/marketplace/collections/<id>/fork` | Fork into an independent copy. Body `{name?, description?}` (`201`) |
| POST | `/v1/marketplace/collections/<id>/rate` | Rate. Body `{rating, comment?}` |
| GET/PUT | `/v1/marketplace/collections/<id>/targeted-users` | Read / replace the targeted user list (`{user_ids}`) |

Unsubscribe is `DELETE /v1/marketplace/subscriptions/<subscription_id>` (a reference held by an agent-pack subscription is refused with `409 managed_by_agent_pack` — see §3.14).

**Extensions**

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/extensions/<ext_id>/publish` | Publish a user extension. Body `{visibility?, user_ids?}` (`409` if already published) |
| GET | `/v1/marketplace/extensions` | Browse. Query `page`, `per_page`, `search`, `sort_by`, `category`, `tier`, `visibility` |
| GET/DELETE | `/v1/marketplace/extensions/<marketplace_id>` | Detail / unpublish (publisher only) |
| POST | `/v1/marketplace/extensions/<marketplace_id>/install` | Install into the user's extensions directory and hot-reload |
| POST | `/v1/marketplace/extensions/<marketplace_id>/rate` | Rate. Body `{rating, comment?}` |

**Component instances (flows, dashboards, canvases)** — `component_instance_marketplace_routes.py`

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/components/<component_id>/instances/<instance_id>/publish` | Publish an instance. Body `{name?, description?, visibility, user_ids?}` |
| POST | `/v1/components/<component_id>/instances/import` | Import an exported instance, two-phase: `dry_run: true` returns a preflight (auto-mapped references + decisions needed); `dry_run: false` commits with the dialog's `mapping` (`mode`, `target_id` for an update) |
| GET | `/v1/marketplace/component-instances` | Browse. Query `component_id`, `search`, `sort_by`, `visibility`, `page`, `per_page` |
| GET | `/v1/marketplace/component-instances/mine` | The caller's own listings |
| GET/DELETE | `/v1/marketplace/component-instances/<marketplace_id>` | Detail / unpublish |
| POST | `/v1/marketplace/component-instances/<marketplace_id>/install` | Fork into a local copy (two-phase `dry_run` + `mapping`, optional `name`) |
| POST/DELETE | `/v1/marketplace/component-instances/<marketplace_id>/subscribe` | Subscribe = materialize a linked local copy (two-phase) / drop the link (the copy stays) |
| POST | `/v1/marketplace/component-instances/<marketplace_id>/sync` | Re-materialize the subscribed copy in place with the mapping captured at subscribe |
| POST | `/v1/marketplace/component-instances/<marketplace_id>/rate` | Rate |
| GET/PUT | `/v1/marketplace/component-instances/<marketplace_id>/targeted-users` | Targeted users |

**Targeted sharing grants** (collections and agent packs)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/marketplace/shareable-users?search=` | Users eligible for targeted sharing (`marketplace_visible`) |
| POST | `/v1/marketplace/share` | Grant. Body `{resource_type: "collection" \| "agent_pack", resource_id, user_ids}` (`201`) |
| GET | `/v1/marketplace/share/<resource_type>/<resource_id>` | Current grants (owner only) |
| DELETE | `/v1/marketplace/share/<grant_id>` | Revoke (grantor only) |

---

### 3.46a. Needs Input (PROP-011) — a turn that stops to ask

A turn that cannot go on without the person ends `input_required` (an interrupted state, never
terminal) with a question record; the person's answer continues it. A REST client that sends
`X-Uderia-Accepts: input_required` on `POST /v1/sessions/<id>/query` and `GET /v1/tasks/<id>` sees
the state and the question (`result.input_request` with its `resume` binding); a client that does
not sees `complete` with `input_required: true`. Contract: [NEEDS_INPUT_ARCHITECTURE.md](../Architecture/NEEDS_INPUT_ARCHITECTURE.md).

| Method | Path | Description |
|---|---|---|
| GET | `/v1/input-requests` | The caller's questions (`?status=pending` default · `all`, `?session_id=`, `?profile_id=`) with `pending`, the count across every agent |
| GET | `/v1/input-requests/<id>` | One question, with its resume binding for the owner |
| GET | `/v1/input-requests/stats` | Asked · answered · declined · expired · median answer time (`?days=`, `?profile_id=`) |
| POST | `/v1/input-requests/<id>/answer` | The person's act — body `{decision: accept·decline·cancel, answer: {header: value}, resume, remember?: session·agent}`; the single-use binding is consumed and the re-entry turn started (a scheduled run resumes through the scheduler, a paused flow at its node); `202` with the new `task_id` |
| POST | `/v1/input-requests/<id>/withdraw` | Decline — the turn ends unanswered and says so |
| POST | `/v1/tasks/<id>/input` | The A2A-shaped alias of `answer` for the task that stopped |

`answer` and `withdraw` are `human_only` operations — listed in the platform catalog, never callable by an agent.

### 3.47. Additional Endpoint Reference

Endpoints of families documented elsewhere in §3 that had no entry. All require auth unless stated;
"admin" means `require_admin` (non-admins get `403`).

**Sessions, profiles and agents**

| Method | Path | Purpose |
|---|---|---|
| PUT | `/v1/sessions/<id>/archive` | Archive a session manually. Body `{archived_reason?}` |
| POST | `/v1/sessions/<id>/next-step-rebind` | Rebuild the free (0-token) Next-Step chips of an executed turn for a different profile. Body `{profile_id, turn_id?}` |
| GET | `/v1/profiles/<id>/skills` | Skills that apply to the profile — the same auto-assign resolution the runtime uses (companion skills follow their components) |
| GET/PUT | `/v1/profiles/<id>/connector-settings` · `/<server_id>` | The profile's platform-connector opt-in state / set `{opted_in, user_tools}` for one connector |
| GET | `/v1/profiles/<id>/learning-repository` | Where this Optimize agent learns: its own planner repository or the shared default |
| POST | `/v1/profiles/<id>/learning-repository` | Opt an existing Optimize agent into its own planner repository (idempotent; the signature does not move). `400` for non-Optimize agents or an agent without an MCP server |
| GET/PUT | `/v1/user/component-profile-defaults` | The caller's per-component defaults for NEW profiles (`{component_id: bool}`) |
| PUT | `/v1/skills/activations/<name>/config` · `/rename` | Update a skill activation's `{config?, default_param?}` / rename it (`{new_name}`; `409` on a clash) |
| POST | `/v1/agent-packs/create-and-install` | Create a pack from existing `profile_ids` (`name`, `description?`) and install it locally by REFERENCE — uninstalling it does not delete the original profiles |
| GET/DELETE | `/v1/marketplace/agent-packs/<pack_id>` | Listing detail incl. manifest summary / unpublish (publisher) |
| DELETE | `/v1/skills/activations/<name>` | Hard-delete a skill activation (deactivate is §3.21) |
| GET | `/v1/credentials` | Providers the caller has stored credentials for (`{providers: [...]}`) |
| GET/POST/DELETE | `/v1/credentials/<provider>` | Whether credentials exist (values never returned) / store `{"credentials": {...}}` (Fernet-encrypted) / delete |
| GET | `/v1/obda/planes` | Which OBDA planes (OBDA-READ · OBDA-MODEL · OBDA-ACT) the caller can reach — component availability AND the knowledge-graph feature for the tier. Response `{planes: ...}`; ask this before rendering a plane's control |

**Knowledge repositories and planner collections**

| Method | Path | Purpose |
|---|---|---|
| PATCH | `/v1/knowledge/repositories/<id>/documents/<doc_id>` | A document's CDC sync configuration: `source_uri` (null clears), `sync_enabled`, `sync_interval` (`hourly` · `6h` · `daily` · `weekly`, applies to the collection) |
| GET | `/v1/rag/collections/<id>` | One collection |
| POST | `/v1/rag/collections/import` | Import an exported `.zip` (multipart `file`, or JSON `{import_path}` on the server); optional `mcp_server_id`, `vector_store_config_id` |
| POST | `/v1/knowledge/repositories/<id>/sync` | Run a CDC sync now for every sync-enabled document: `{checked, updated, unchanged, errors, duration_seconds}` |
| POST | `/v1/knowledge/repositories/<id>/reindex` | Re-embed every document with the current embedding model (`strategy` chosen automatically: in place when the repository is listed or subscribed) |
| POST | `/v1/knowledge/repositories/<id>/scan` | Backfill the ingest screen over unscanned documents (`{"rescan": true}` = all). Requires guardrails enabled |
| GET | `/v1/knowledge/repositories/<id>/scan-manifest` | Corpus Health: scan coverage, findings by category (labels and counts only), PII manifest, quarantine state, the `scan_report_hash` the earned signature binds |
| POST | `/v1/knowledge/documents/<doc_id>/quarantine` · `/unquarantine` | Exclude a document from retrieval (it stays visible) / reverse it. Body `{reason}`; a trust event that moves the repository's signature |
| POST | `/v1/knowledge/documents/<doc_id>/acknowledge-finding` | Acknowledge a critical (injection) finding with a REQUIRED `{reason}`; frozen into the scan record and the earned signature |
| GET | `/v1/rag/collections/<id>/rows` | Cases of a planner collection. Query `limit` (25, max 10000), `q`, `light`, plus filter/sort fields |
| DELETE | `/v1/rag/collections/<id>/rows/<case_id>` | Remove one case from a planner repository the caller owns (not pack-managed) |
| GET | `/v1/rag/collections/<id>/scan-manifest` | Planner Health: every champion case the signing gate flags (`findings[]` — case id, question, labels, the argument and an excerpt of the text that tripped the scanner, `acknowledged`), the counts `open` · `acknowledged` · `caveat_only`, and the gate's own `verdict`. Owner-only |
| POST | `/v1/rag/collections/<id>/rows/<case_id>/acknowledge-finding` | Acknowledge the gate's finding on ONE champion case with a REQUIRED `{reason}`; stamped on the case (`trust_ack`, both stores) so it enters the repository's signature — the signature reads changed until re-signed. 409 when the case carries no open finding |
| POST | `/v1/rag/collections/<id>/reset` | Remove all content, keep the collection |
| POST | `/v1/rag/collections/<id>/refresh-counts` | Re-read document/chunk counts from the backend |
| GET | `/v1/rag/collections/<id>/export` | Export as a portable `.zip` (`export_path` for a server-side target) |
| POST | `/v1/rag/reload-collections` | Reload all enabled collections into the retriever |
| GET | `/v1/rag/templates/list` · `/discover` · `/<id>/full` | Template list / the loadable plugins under `rag_templates/templates/` / full template JSON |
| GET/PUT | `/v1/rag/templates/<id>/config` | Editable template configuration |
| GET/POST/DELETE | `/v1/rag/templates/<id>/defaults` | The caller's template parameter defaults (`{defaults}`) / reset to system defaults |
| POST | `/v1/rag/templates/reload` · `/validate` | Hot-reload plugins / validate a plugin at `{plugin_path}` (see *Managing Templates*) |

**Knowledge graphs and OBDA-MODEL** (§3.35)

| Method | Path | Purpose |
|---|---|---|
| PATCH | `/v1/knowledge-graph/assignments/activate` | Make one KG active for a profile. Body `{kg_id, assigned_profile_id, kg_owner_profile_id?}` |
| POST | `/v1/knowledge-graph/import-rdf` | Import Turtle / JSON-LD / RDF-XML / N-Triples (lossless for Uderia's own export; `warnings` for a foreign graph). The request body is the raw RDF document; query `serialization` (default `turtle`), `profile_id`, `target_kg_id` (re-import into an existing graph), `kg_name`, `kg_description` |
| GET | `/v1/knowledge-graph/andp-products?connection_id=` | AI-Native Data Products deployed on a database connection (constructor picker) |
| POST | `/v1/knowledge-graph/reload-demo-data` | Regenerate and reload the demo rows of a DEPLOYED industry-model KG (data only) |
| POST | `/v1/knowledge-graph/propose` | OBDA-MODEL: draft a proposed attribute/entity through the same handler a turn runs. Body `{kg_id, action, ...}`; `422` when the advisor refuses |
| POST | `/v1/knowledge-graph/propose/suggest` | OBDA-MODEL: what a missing concept probably records, in plain words (one small call on the answering agent's model, booked to that turn). Body `{kg_id, name, definition?, question?, profile_id?, session_id?, turn_id?}` |
| GET | `/v1/knowledge-graph/metrics/<metric_name>/export` | One semantic metric as W3C RDF Data Cube + PROV (+ DCAT): `?format=qb\|prov\|dcat\|all`, `?serialization=turtle\|jsonld\|xml\|nt`, `?include_sql=true` |
| POST | `/v1/actions/access` | OBDA-ACT: what an action needs in the warehouse (write login holds the action's role; the role holds exactly the operation's rights + INSERT on the ledger), held or missing, before it is defined, validated or performed |
| GET | `/v1/ontologies` | The caller's PUBLISHED ontology products |
| GET | `/v1/ontologies/<kg_id>` | The published version, content-negotiated: `?format=owl\|skos\|rdf`, `?serialization=` (Turtle/JSON-LD/RDF-XML/N-Triples) or native JSON; `?version=` pins a snapshot |
| GET | `/v1/action-targets?kg_id=` · `POST /v1/action-targets/describe` | OBDA-ACT authoring: the deployed entities an action may write / what an action on one entity can write (table, key, column contract types; live types with `connection_id`) |
| GET | `/v1/platform-ontology/segment` · `/users` | The caller's Platform Ontology segment as a graph spec / the user list for the admin scope selector (admin; Ontology Suite, §3.39) |

**Agent packs** (§3.14)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/agent-packs/<installation_id>/lifecycle` | Lifecycle state, audit trail and active policy |
| POST | `/v1/agent-packs/<installation_id>/lifecycle/<action>` | `propose` · `approve` · `reject` · `publish` · `deprecate`. Body `{sla_tier?, override?}`; a denied transition is `409` (audited) |
| POST | `/v1/agent-packs/<installation_id>/sla` | Owner: set/clear the pack's SLA tier (`{sla_tier}`) |
| GET/PUT | `/v1/agent-packs/governance/policy` | The pack-governance policy (PUT admin only) |

**Flows, connectors, evaluation, cost, configuration**

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/flows/<flow_id>/versions/<version_id>/restore` | Restore a flow definition from a snapshot |
| POST/DELETE | `/v1/connectors/<platform>/token` | Save / remove a user API key or token for a non-OAuth connector (Fernet-encrypted in `messaging_identities`) |
| POST | `/v1/mcp/servers/import` | Import MCP server(s) from an MCP Registry `server.json` or a Claude Desktop `mcpServers` block |
| GET/POST | `/v1/admin/app-config` | Admin: application configuration (feature availability and the `trust_behavior`, `kg_config`, `agent_eval.*` blocks described in §3.39) |
| GET | `/v1/admin/source-endpoints` | Admin: every data-source endpoint users' MCP servers and database connections reach — for writing a deployment-wide combine isolation rule (PROP-006); names only, never a credential |
| GET/POST | `/v1/admin/component-settings` · `/v1/admin/kg-marketplace-settings` | Admin: component governance (global gate, disabled list) / KG marketplace switch |
| GET | `/v1/costs/coverage` | Admin: which models in use have a price; `?fill=1` first fills what the price sources can (`CostManager.fill_coverage`) |
| GET | `/v1/config/document-upload-capabilities[/<provider>]` | Native document-upload support per provider (`?model=` for model-specific) |
| GET/PUT | `/v1/config/classification` | The GLOBAL `enable_mcp_classification` flag (affects every user) |
| GET | `/v1/config/stored-credentials` | Which credentials the caller has stored (metadata only) |
| POST | `/v1/credentials/<provider>/test` | Test the caller's stored credentials for a provider (`501` where no test exists) |
| GET | `/v1/consumption_warnings` | The caller's highest consumption usage vs hourly/daily/monthly limits (welcome screen) |
| GET | `/v1/auth/me/audit-logs` | The caller's own audit log. Query `limit` (100), `offset`, `action`. Response `{logs, total}`; each row carries its structured `metadata` |
| POST | `/v1/contact/submit` | Promotional-website contact form (no auth) |

---

### 3.48. Web-UI Internal Routes

These routes serve the single-page web UI. They are not a stable API — automation should use the
`/api/v1/*` equivalents in §3. Listed so that every route the server registers is accounted for
(`api/routes.py`).

| Method | Path | Serves | Auth |
|---|---|---|---|
| GET | `/` · `/login` · `/register` · `/reset-password` · `/verify-email` | HTML pages | none |
| GET | `/api/status` · `/app-config` · `/app-settings` | Startup state, flags and license info, public UI settings | none / optional |
| POST | `/configure` · `/test-mcp-connection` · `/models` | Legacy configuration, MCP connectivity test, provider model list | jwt (configure) |
| GET | `/api_key/<provider>` | Provider API keys from the **server's environment variables**, for pre-filling the LLM form | jwt + admin (`403` otherwise) |
| POST | `/api/notifications/ticket` | A one-minute ticket that opens the caller's notification stream (`{ticket}`) | jwt |
| GET | `/api/notifications/subscribe?ticket=…` | The per-user notification stream (SSE, 20 s heartbeat); the user is the ticket's — a missing/invalid/expired ticket is `401` | ticket |
| GET | `/tools` · `/prompts` · `/connectors` · `/resources` | The resource panel's catalog for `?profile_id=` or the caller's default agent (`/resources` is a placeholder) | jwt |
| POST/GET | `/session` · `/sessions` | Create / list sessions (UI); REST clients use `/api/v1/sessions` | jwt |
| POST | `/api/session/<id>/rename` · `/purge_memory` | Rename a session / purge its LLM context (`chat_object`; genie children too) | jwt |
| GET | `/api/session/<id>/turn/<n>/plan` · `/query` | A turn's original plan / query | jwt |
| POST | `/api/session/<id>/turn/<n>/toggle_validity` | Mark a turn valid/invalid for context | jwt |
| POST | `/simple_chat` · `/invoke_prompt_stream` | Tool-less chat modal / direct MCP-prompt invocation (SSE) | jwt |
| GET | `/system_prompt/<provider>/<model>` · `/api/prompts-version` | Resolved system prompt for a model / hash of the master prompts | jwt / none |
| GET | `/api/questions` · `/rag/collections` · `/rag/collections/<id>/rows` · `/rag/cases/<case_id>` | RAG question suggestions, planner collection list/rows, one case | mixed |
| POST | `/api/rag/cases/<case_id>/feedback` | Up/down-vote a RAG case | jwt |
| POST | `/api/synthesize-speech` | Text-to-speech (Google Cloud TTS) | jwt |
| GET/POST/DELETE | `/api/user/tts-credentials[/test]` | The caller's own TTS credentials | jwt |

> **Note — `/api_key/<provider>`.** Returns the provider keys the server process has in its
> environment (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, Azure/Friendli/AWS values, ...).
> These are deployment secrets, so only an admin may read them; for any other user the route answers
> `403` and the LLM form simply is not pre-filled.
>
> **Note — the notification stream.** `EventSource` cannot send an Authorization header, so a client
> first calls `POST /api/notifications/ticket` with its bearer token and then opens
> `/api/notifications/subscribe?ticket=<ticket>`. The ticket lives 60 s (only long enough to open the
> stream — a reconnect fetches a new one), names the caller, and is never accepted as a bearer token.
> A `user_uuid` query parameter is no longer read.


## 4. Data Models

### 4.1. The Task Object

The Task Object is the central data structure for monitoring a query. It is returned by the `GET /api/v1/tasks/{task_id}` endpoint.

**Structure:**
```json
{
  "task_id": "string",
  "status": "string",
  "last_updated": "string (ISO 8601 UTC)",
  "events": [
    {
      "timestamp": "string (ISO 8601 UTC)",
      "event_data": { ... },
      "event_type": "string"
    }
  ],
  "intermediate_data": [
    {
      "tool_name": "string",
      "data": [ ... ]
    }
  ],
  "result": { ... }
}
```

**Fields:**
- `task_id`: Unique task identifier
- `status`: Task state — `pending` → `processing` → one of `complete` · `error` · `cancelled` (`cancelling` is the transient value right after `POST /tasks/{id}/cancel`). `error` is also used when the turn answered nothing (§3.6.1)
- `session_id`, `user_uuid`, `profile_id_override`: the session the task runs in, its owner, and the `profile_id` override sent on the query (or `null`)
- `last_updated`: UTC timestamp of last update
- `events`: Chronological execution log
- `intermediate_data`: Tool call results as they are generated
- `result`: Final output (null until complete)

**Event Types:**

| Event Type       | Description                                    |
|------------------|------------------------------------------------|
| `plan_generated` | Strategic plan created or revised              |
| `phase_start`    | New phase of execution beginning               |
| `tool_result`    | Tool execution completed                       |
| `token_update`   | LLM tokens consumed                            |
| `workaround`     | Self-correction or optimization performed      |
| `cancelled`      | Execution stopped (user request)               |
| `error`          | Error occurred during execution                |

**Result Object Schemas:**

*CanonicalResponse* (standard queries):
```json
{
  "direct_answer": "string",
  "key_metric": { "value": "string", "label": "string" } | null,
  "key_observations": [ { "text": "string" } ],
  "synthesis": [ { "text": "string" } ]
}
```

*PromptReportResponse* (pre-defined prompts):
```json
{
  "title": "string",
  "executive_summary": "string",
  "report_sections": [
    { "title": "string", "content": "string (Markdown)" }
  ]
}
```

### 4.2. Authentication Token Comparison

| Feature | Access Tokens | JWT Tokens |
|---------|---------------|------------|
| **Format** | `tda_xxxxx...` (42 chars) | `eyJhbGci...` (variable) |
| **Lifetime** | Configurable or never | 24 hours fixed |
| **Use Case** | REST API, automation | Web UI sessions |
| **Storage** | SHA256 hashed | Stateless (not stored) |
| **Revocation** | Manual (instant) | Automatic (24h expiry) |
| **Tracking** | Use count, last used | No tracking |
| **Multiple** | Yes (per app/env) | One per session |
| **Security** | Shown once only | Regenerate on login |
| **Best For** | CI/CD, scripts, integrations | Interactive web use |

## 5. Code Examples

### 5.1. Complete Python Client

```python
#!/usr/bin/env python3
"""
Comprehensive TDA Python Client with access token management.
"""
import requests
import time
import os
from typing import Optional, Dict, Any

class TDAClient:
    """Uderia Platform API Client"""
    
    def __init__(self, base_url: str = "http://localhost:5050"):
        self.base_url = base_url.rstrip('/')
        self.jwt_token: Optional[str] = None
        self.access_token: Optional[str] = None
        self.session_id: Optional[str] = None
    
    def _get_headers(self) -> Dict[str, str]:
        """Get authentication headers"""
        token = self.access_token or self.jwt_token
        if not token:
            raise Exception("Not authenticated. Call login() or set_access_token() first.")
        
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    def register(self, username: str, email: str, password: str) -> Dict[str, Any]:
        """Register a new user account"""
        response = requests.post(
            f"{self.base_url}/api/v1/auth/register",
            json={
                "username": username,
                "email": email,
                "password": password
            }
        )
        response.raise_for_status()
        return response.json()
    
    def login(self, username: str, password: str) -> bool:
        """Login and store JWT token"""
        response = requests.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"username": username, "password": password}
        )
        
        if response.status_code == 200:
            data = response.json()
            self.jwt_token = data.get("token")
            return True
        return False
    
    def set_access_token(self, token: str):
        """Set long-lived access token for authentication"""
        self.access_token = token
    
    def create_access_token(self, name: str, expires_in_days: Optional[int] = 90) -> Dict[str, Any]:
        """Create a new access token"""
        response = requests.post(
            f"{self.base_url}/api/v1/auth/tokens",
            headers=self._get_headers(),
            json={
                "name": name,
                "expires_in_days": expires_in_days
            }
        )
        response.raise_for_status()
        return response.json()
    
    def list_access_tokens(self) -> Dict[str, Any]:
        """List all access tokens"""
        response = requests.get(
            f"{self.base_url}/api/v1/auth/tokens",
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()
    
    def revoke_access_token(self, token_id: str) -> Dict[str, Any]:
        """Revoke an access token"""
        response = requests.delete(
            f"{self.base_url}/api/v1/auth/tokens/{token_id}",
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()
    
    def configure(self, provider: str, model: str, credentials: Dict[str, str], 
                  mcp_server: Dict[str, Any]) -> Dict[str, Any]:
        """Configure LLM and MCP server"""
        response = requests.post(
            f"{self.base_url}/api/v1/configure",
            json={
                "provider": provider,
                "model": model,
                "credentials": credentials,
                "mcp_server": mcp_server
            }
        )
        response.raise_for_status()
        return response.json()
    
    def create_session(self) -> str:
        """Create a new session and return session ID"""
        response = requests.post(
            f"{self.base_url}/api/v1/sessions",
            headers=self._get_headers()
        )
        response.raise_for_status()
        data = response.json()
        self.session_id = data.get("session_id")
        return self.session_id
    
    def submit_query(self, prompt: str, session_id: Optional[str] = None, 
                    profile_id: Optional[str] = None) -> Dict[str, Any]:
        """Submit a query and return task info
        
        Args:
            prompt: The natural language query
            session_id: Session ID (uses self.session_id if not provided)
            profile_id: Optional profile override (if omitted, uses default profile)
        
        Returns:
            Task information with task_id and status_url
        """
        sid = session_id or self.session_id
        if not sid:
            raise Exception("No session ID. Call create_session() first.")
        
        payload = {"prompt": prompt}
        if profile_id:
            payload["profile_id"] = profile_id
        
        response = requests.post(
            f"{self.base_url}/api/v1/sessions/{sid}/query",
            headers=self._get_headers(),
            json=payload
        )
        response.raise_for_status()
        return response.json()
    
    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get current task status"""
        response = requests.get(
            f"{self.base_url}/api/v1/tasks/{task_id}",
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()
    
    def wait_for_task(self, task_id: str, poll_interval: int = 2, 
                     max_wait: int = 300) -> Dict[str, Any]:
        """Wait for task completion with polling"""
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            task = self.get_task_status(task_id)
            status = task.get("status")
            
            if status in ["complete", "error", "cancelled"]:
                return task
            
            time.sleep(poll_interval)
        
        raise TimeoutError(f"Task {task_id} did not complete within {max_wait} seconds")
    
    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        """Cancel a running task"""
        response = requests.post(
            f"{self.base_url}/api/v1/tasks/{task_id}/cancel",
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()
    
    def execute_query(self, prompt: str, session_id: Optional[str] = None, 
                     wait: bool = True, profile_id: Optional[str] = None) -> Dict[str, Any]:
        """Execute a query and optionally wait for completion
        
        Args:
            prompt: The natural language query
            session_id: Session ID (uses self.session_id if not provided)
            wait: Whether to wait for completion
            profile_id: Optional profile override
        
        Returns:
            Task information or final result if wait=True
        """
        task_info = self.submit_query(prompt, session_id, profile_id)
        task_id = task_info.get("task_id")
        
        if wait:
            return self.wait_for_task(task_id)
        return task_info

# Usage Example
if __name__ == "__main__":
    # Initialize client
    client = TDAClient()
    
    # Option 1: Use access token (recommended)
    client.set_access_token(os.getenv("TDA_ACCESS_TOKEN"))
    
    # Option 2: Login with credentials
    # client.login("username", "password")
    
    # Create session and execute queries
    session_id = client.create_session()
    print(f"Created session: {session_id}")
    
    # Query 1: Using default profile
    result1 = client.execute_query("Show me all available databases")
    if result1["status"] == "complete":
        print("Query 1 completed with default profile!")
    
    # Query 2: Using profile override (execute same session with different profile)
    result2 = client.execute_query(
        "Tell me about the current configuration",
        profile_id="profile-1764006444002-z0hdduce9"  # Override with different profile
    )
    if result2["status"] == "complete":
        print("Query 2 completed with override profile!")
```

### 5.2. Bash/Shell Scripts

**Complete Automation Script:**
```bash
#!/bin/bash
# automated_analysis.sh - Daily database analysis automation

set -euo pipefail

# Configuration
TDA_URL="${TDA_URL:-http://localhost:5050}"
TDA_TOKEN="${TDA_ACCESS_TOKEN}"
DATABASE="${DATABASE:-production}"

if [ -z "$TDA_TOKEN" ]; then
    echo "Error: TDA_ACCESS_TOKEN environment variable not set"
    exit 1
fi

# Helper function for API calls
api_call() {
    local method="$1"
    local endpoint="$2"
    local data="${3:-}"
    
    if [ -z "$data" ]; then
        curl -s -X "$method" "$TDA_URL$endpoint" \
            -H "Authorization: Bearer $TDA_TOKEN"
    else
        curl -s -X "$method" "$TDA_URL$endpoint" \
            -H "Authorization: Bearer $TDA_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$data"
    fi
}

# Create session
echo "Creating session..."
SESSION_ID=$(api_call POST "/api/v1/sessions" | jq -r '.session_id')
echo "Session ID: $SESSION_ID"

# Submit query
echo "Submitting query..."
QUERY="Analyze the $DATABASE database and provide key metrics"
TASK_INFO=$(api_call POST "/api/v1/sessions/$SESSION_ID/query" \
    "{\"prompt\": \"$QUERY\"}")
TASK_ID=$(echo "$TASK_INFO" | jq -r '.task_id')
echo "Task ID: $TASK_ID"

# Poll for completion
echo "Waiting for results..."
while true; do
    TASK_STATUS=$(api_call GET "/api/v1/tasks/$TASK_ID")
    STATUS=$(echo "$TASK_STATUS" | jq -r '.status')
    
    echo "Status: $STATUS"
    
    if [ "$STATUS" = "complete" ]; then
        echo "✓ Query completed successfully"
        echo "$TASK_STATUS" | jq '.result'
        break
    elif [ "$STATUS" = "error" ]; then
        echo "✗ Query failed"
        echo "$TASK_STATUS" | jq '.result'
        exit 1
    elif [ "$STATUS" = "cancelled" ]; then
        echo "⚠ Query was cancelled"
        exit 1
    fi
    
    sleep 2
done
```

### 5.3. JavaScript/Node.js Client

```javascript
const axios = require('axios');

class TDAClient {
    constructor(baseURL = 'http://localhost:5050') {
        this.baseURL = baseURL;
        this.jwtToken = null;
        this.accessToken = null;
        this.sessionId = null;
    }
    
    getHeaders() {
        const token = this.accessToken || this.jwtToken;
        if (!token) {
            throw new Error('Not authenticated');
        }
        
        return {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
        };
    }
    
    async register(username, email, password) {
        const response = await axios.post(
            `${this.baseURL}/api/v1/auth/register`,
            { username, email, password }
        );
        return response.data;
    }
    
    async login(username, password) {
        try {
            const response = await axios.post(
                `${this.baseURL}/api/v1/auth/login`,
                { username, password }
            );
            
            if (response.data.status === 'success') {
                this.jwtToken = response.data.token;
                return true;
            }
            return false;
        } catch (error) {
            console.error('Login failed:', error.message);
            return false;
        }
    }
    
    setAccessToken(token) {
        this.accessToken = token;
    }
    
    async createAccessToken(name, expiresInDays = 90) {
        const response = await axios.post(
            `${this.baseURL}/api/v1/auth/tokens`,
            { name, expires_in_days: expiresInDays },
            { headers: this.getHeaders() }
        );
        return response.data;
    }
    
    async createSession() {
        const response = await axios.post(
            `${this.baseURL}/api/v1/sessions`,
            {},
            { headers: this.getHeaders() }
        );
        this.sessionId = response.data.session_id;
        return this.sessionId;
    }
    
    async submitQuery(prompt, sessionId = null) {
        const sid = sessionId || this.sessionId;
        if (!sid) {
            throw new Error('No session ID');
        }
        
        const response = await axios.post(
            `${this.baseURL}/api/v1/sessions/${sid}/query`,
            { prompt },
            { headers: this.getHeaders() }
        );
        return response.data;
    }
    
    async getTaskStatus(taskId) {
        const response = await axios.get(
            `${this.baseURL}/api/v1/tasks/${taskId}`,
            { headers: this.getHeaders() }
        );
        return response.data;
    }
    
    async waitForTask(taskId, pollInterval = 2000, maxWait = 300000) {
        const startTime = Date.now();
        
        while (Date.now() - startTime < maxWait) {
            const task = await this.getTaskStatus(taskId);
            const status = task.status;
            
            if (['complete', 'error', 'cancelled'].includes(status)) {
                return task;
            }
            
            await new Promise(resolve => setTimeout(resolve, pollInterval));
        }
        
        throw new Error(`Task ${taskId} timeout after ${maxWait}ms`);
    }
    
    async executeQuery(prompt, sessionId = null, wait = true) {
        const taskInfo = await this.submitQuery(prompt, sessionId);
        const taskId = taskInfo.task_id;
        
        if (wait) {
            return await this.waitForTask(taskId);
        }
        return taskInfo;
    }
}

// Usage
(async () => {
    const client = new TDAClient();
    
    // Use access token
    client.setAccessToken(process.env.TDA_ACCESS_TOKEN);
    
    // Create session and execute query
    const sessionId = await client.createSession();
    console.log(`Created session: ${sessionId}`);
    
    const result = await client.executeQuery('Show me all databases');
    
    if (result.status === 'complete') {
        console.log('Query completed!');
        console.log('Result:', result.result);
    }
})();
```

### 5.4. cURL Examples

**Create Access Token:**
```bash
# Login first
JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"your_user","password":"your_pass"}' \
  | jq -r '.token')

# Create token
TOKEN=$(curl -s -X POST http://localhost:5050/api/v1/auth/tokens \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"name":"Production","expires_in_days":90}' \
  | jq -r '.token')

echo "Your access token: $TOKEN"
echo "Save this token securely!"
```

**Complete Query Workflow:**
```bash
TOKEN="tda_your_token_here"

# Create session
SESSION=$(curl -s -X POST http://localhost:5050/api/v1/sessions \
  -H "Authorization: Bearer $TOKEN" \
  | jq -r '.session_id')

# Submit query
TASK=$(curl -s -X POST http://localhost:5050/api/v1/sessions/$SESSION/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Show all databases"}' \
  | jq -r '.task_id')

# Poll for result
while true; do
    STATUS=$(curl -s http://localhost:5050/api/v1/tasks/$TASK \
      -H "Authorization: Bearer $TOKEN" \
      | jq -r '.status')
    
    if [ "$STATUS" = "complete" ]; then
        curl -s http://localhost:5050/api/v1/tasks/$TASK \
          -H "Authorization: Bearer $TOKEN" | jq '.result'
        break
    fi
    
    sleep 2
done
```

## 6. Security Best Practices

### 6.1. Token Storage

#### ✅ Recommended Approaches

**Environment Variables:**
```bash
# ~/.bashrc or ~/.zshrc
export TDA_ACCESS_TOKEN="tda_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"
export TDA_BASE_URL="https://tda.company.com"
```

**.env Files (with .gitignore):**
```bash
# .env
TDA_ACCESS_TOKEN=tda_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p
TDA_BASE_URL=https://tda.company.com
```

```python
# Python with python-dotenv
from dotenv import load_dotenv
import os

load_dotenv()
TOKEN = os.getenv('TDA_ACCESS_TOKEN')
```

**Secret Managers:**
```python
# AWS Secrets Manager
import boto3
secrets = boto3.client('secretsmanager')
token = secrets.get_secret_value(SecretId='tda/access_token')['SecretString']

# Azure Key Vault
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
client = SecretClient(vault_url="https://myvault.vault.azure.net", 
                     credential=DefaultAzureCredential())
token = client.get_secret("tda-access-token").value
```

#### ❌ Bad Practices

```python
# DON'T hardcode tokens
TOKEN = "tda_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"

# DON'T commit tokens to git
# DON'T store tokens in plain text files
# DON'T log tokens in application logs
# DON'T share tokens via email/chat
```

### 6.2. Network Security

**Use HTTPS in Production:**
```python
# ✅ Production
BASE_URL = "https://tda.company.com"

# ⚠️ Development only
BASE_URL = "http://localhost:5050"
```

**Certificate Verification:**
```python
import requests

# ✅ Verify certificates (default)
response = requests.get(url, verify=True)

# ⚠️ Only disable for local development
response = requests.get(url, verify=False)
```

### 6.3. Token Management

**Create Separate Tokens per Application:**
```bash
# Production server
TOKEN_PROD=$(create_token "Production Server" 90)

# Development server
TOKEN_DEV=$(create_token "Development Server" 30)

# CI/CD Pipeline
TOKEN_CI=$(create_token "CI/CD Pipeline" 365)
```

**Rotate Tokens Regularly:**
```bash
#!/bin/bash
# rotate_tokens.sh - Automate token rotation

OLD_TOKEN_ID="token-id-to-revoke"
NEW_TOKEN_NAME="Production Server ($(date +%Y-%m-%d))"

# Create new token
NEW_TOKEN=$(create_access_token "$NEW_TOKEN_NAME" 90)

# Update application configuration
update_app_config "$NEW_TOKEN"

# Verify new token works
test_api_with_token "$NEW_TOKEN"

# Revoke old token only after verification
revoke_token "$OLD_TOKEN_ID"
```

**Implement Token Expiration Handling:**
```python
import time

class TDAClient:
    def __init__(self):
        self.token = None
        self.token_expires_at = 0
    
    def is_token_valid(self):
        if not self.token:
            return False
        
        # For access tokens with known expiration
        if self.token_expires_at:
            return time.time() < self.token_expires_at
        
        return True
    
    def execute_with_retry(self, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                # Token expired - refresh/re-login
                self.refresh_authentication()
                return func(*args, **kwargs)
            raise
```

### 6.4. Rate Limiting

The API implements rate limiting to prevent abuse:

| Endpoint | Limit | Window |
|----------|-------|--------|
| `/api/v1/auth/register` | 3 requests | 1 hour per IP |
| `/api/v1/auth/login` | 5 requests | 1 minute per IP |
| All other endpoints | Varies | Based on usage |

**Handle Rate Limits:**
```python
import time

def api_call_with_backoff(func, max_retries=3):
    for attempt in range(max_retries):
        try:
            return func()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                retry_after = int(e.response.headers.get('Retry-After', 60))
                print(f"Rate limited. Waiting {retry_after}s...")
                time.sleep(retry_after)
            else:
                raise
    raise Exception("Max retries exceeded")
```

### 6.5. Audit Logging

**Monitor Token Usage:**
```python
def list_token_usage():
    tokens = client.list_access_tokens()
    
    for token in tokens['tokens']:
        print(f"Token: {token['name']}")
        print(f"  Created: {token['created_at']}")
        print(f"  Last Used: {token['last_used_at']}")
        print(f"  Use Count: {token['use_count']}")
        print(f"  Status: {'Active' if not token['revoked'] else 'Revoked'}")
        
        # Alert on suspicious usage
        if token['use_count'] > 10000:
            alert_security_team(f"High usage detected: {token['name']}")
```

### 6.6. Security Checklist

- [ ] All tokens stored securely (environment variables or secret managers)
- [ ] HTTPS enabled for all production connections
- [ ] Separate tokens created for each application/environment
- [ ] Token expiration set appropriately (90 days for production)
- [ ] Regular token rotation schedule implemented
- [ ] Monitoring and alerting for token usage
- [ ] Rate limit handling implemented
- [ ] No tokens committed to version control
- [ ] Certificate verification enabled
- [ ] Token revocation process documented

---

## 7. Troubleshooting

### 7.1. Authentication Errors

#### "Invalid or revoked access token"

**Cause:** Token doesn't exist, was revoked, or expired  
**Solution:**
```bash
# List your tokens to check status
curl -X GET http://localhost:5050/api/v1/auth/tokens \
  -H "Authorization: Bearer $JWT"

# Create new token if needed
curl -X POST http://localhost:5050/api/v1/auth/tokens \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"name":"New Token","expires_in_days":90}'
```

#### "Authentication required"

**Cause:** Missing or malformed `Authorization` header  
**Solution:**
```bash
# ✅ Correct format
curl -H "Authorization: Bearer tda_xxxxx..."

# ❌ Common mistakes
curl -H "Authorization: tda_xxxxx..."        # Missing "Bearer "
curl -H "Authorization: Bearer tda_xxxxx ..."  # Extra space after token
curl -H "Authorization:Bearer tda_xxxxx..."  # Missing space after colon
```

#### "Token expired"

**Cause:** JWT token older than 24 hours  
**Solution:** Login again to get new JWT token

```python
# Implement auto-refresh
def ensure_authenticated(client):
    try:
        return client.some_api_call()
    except TokenExpiredError:
        client.login(username, password)
        return client.some_api_call()
```

### 7.2. Configuration Errors

#### "No default profile configured for this user"

**HTTP Status:** `400 Bad Request`

**Cause:** User doesn't have a default profile set up. REST operations require a profile that combines an LLM provider with an MCP server.

**Solution:** 
1. Configure a profile through the web UI:
   - Go to **Anatomy**
   - Create LLM Configuration (Google, Anthropic, Azure, AWS, Friendli, or Ollama)
   - Create MCP Server Configuration (point to your data/tools)
   - Create Profile combining both
   - Mark Profile as **default** (star icon)

2. Or configure via REST API (see endpoints in Section 3.3)

#### "Profile is incomplete"

**HTTP Status:** `503 Service Unavailable`

**Cause:** Default profile exists but is missing LLM or MCP server configuration

**Solution:**
1. Edit the profile in Configuration UI
2. Ensure both LLM Configuration ID and MCP Server ID are set
3. Save the profile

#### "MCP server connection failed"

**Cause:** MCP server not running or incorrect configuration  
**Solution:**
1. Verify MCP server is running
2. Check host/port/path configuration in Profile settings
3. Test connection: `curl http://localhost:8001/mcp`

### 7.3. Query Execution Errors

#### "Invalid or non-existent profile_id"

**HTTP Status:** `400 Bad Request`

**Cause:** The `profile_id` specified in the query request doesn't exist or doesn't belong to the user

**Solution:**
1. Get your available profiles:
```bash
curl -X GET http://localhost:5050/api/v1/profiles \
  -H "Authorization: Bearer $TOKEN"
```

2. Use a valid profile ID from the list, or omit `profile_id` to use default:
```bash
# Without override (uses default profile)
curl -X POST http://localhost:5050/api/v1/sessions/{id}/query \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"prompt": "Your question"}'

# With valid override
curl -X POST http://localhost:5050/api/v1/sessions/{id}/query \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"prompt": "Your question", "profile_id": "profile-valid-id"}'
```

#### Task Status: "error"

**Cause:** Query execution failed  
**Solution:** Check the task `result` field for error details

```python
task = client.get_task_status(task_id)
if task['status'] == 'error':
    print("Error details:", task['result'])
    print("Events log:", task['events'])
```

#### Task Never Completes

**Cause:** Long-running query or server issue  
**Solution:**
1. Check task events for progress: `GET /api/v1/tasks/{task_id}`
2. Increase timeout in polling logic
3. Cancel and retry: `POST /api/v1/tasks/{task_id}/cancel`

### 7.4. Network Errors

#### "Connection refused"

**Cause:** TDA server not running or wrong URL  
**Solution:**
```bash
# Check if server is running
curl http://localhost:5050/

# Verify correct port
ps aux | grep trusted_data_agent

# Check logs
tail -f logs/tda.log
```

#### SSL/Certificate Errors

**Cause:** Self-signed certificate or expired cert  
**Solution:**
```python
# Development: Disable verification (not for production!)
import requests
import urllib3
urllib3.disable_warnings()
response = requests.get(url, verify=False)

# Production: Install proper certificate
# or add CA certificate to trusted store
```

### 7.5. Rate Limiting

#### "429 Too Many Requests"

**Cause:** Exceeded rate limits  
**Solution:**
```python
import time

def handle_rate_limit(response):
    if response.status_code == 429:
        retry_after = int(response.headers.get('Retry-After', 60))
        print(f"Rate limited. Waiting {retry_after} seconds...")
        time.sleep(retry_after)
        return True
    return False

# Use in requests
while True:
    response = requests.get(url, headers=headers)
    if not handle_rate_limit(response):
        break
```

### 7.6. Common Issues

#### "Session not found"

**Cause:** Session ID is invalid or expired  
**Solution:** Create new session

```python
session_id = client.create_session()
```

#### "Task not found"

**Cause:** Task ID is invalid or task was purged  
**Solution:** Submit query again

```python
task_info = client.submit_query(prompt)
task_id = task_info['task_id']
```

#### "Invalid JSON"

**Cause:** Malformed request body  
**Solution:** Validate JSON before sending

```python
import json

# Validate JSON
try:
    json.loads(request_body)
except json.JSONDecodeError as e:
    print(f"Invalid JSON: {e}")
```

### 7.7. Debugging Tips

**Enable Verbose Logging:**
```python
import logging
import http.client as http_client

http_client.HTTPConnection.debuglevel = 1
logging.basicConfig(level=logging.DEBUG)
```

**Inspect Full Response:**
```python
response = requests.post(url, headers=headers, json=data)
print("Status:", response.status_code)
print("Headers:", response.headers)
print("Body:", response.text)
```

**Test Authentication:**
```bash
# Verify token is valid
TOKEN="tda_your_token"
curl -v http://localhost:5050/api/v1/sessions \
  -H "Authorization: Bearer $TOKEN"
```

### 7.8. Getting Help

If you continue to experience issues:

1. **Check the logs:** `logs/tda.log` on the server
2. **Verify configuration:** Web UI → Config tab
3. **Test basic connectivity:** `curl http://localhost:5050/`
4. **Check token status:** `GET /api/v1/auth/tokens`
5. **Review error events:** Check task `events` array for detailed execution logs

---

## 8. Quick Reference

### Authentication Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/register` | Register new user |
| POST | `/api/v1/auth/login` | Login and get JWT |
| POST | `/api/v1/auth/logout` | Logout (web UI) |
| POST | `/api/v1/auth/tokens` | Create access token |
| GET | `/api/v1/auth/tokens` | List access tokens |
| DELETE | `/api/v1/auth/tokens/{id}` | Revoke access token |

### Session & Query Endpoints

| Method | Endpoint | Description | Requires Profile |
|--------|----------|-------------|------------------|
| POST | `/api/v1/sessions` | Create new session | ✅ Yes (default) |
| GET | `/api/v1/sessions` | List sessions | ❌ No |
| GET | `/api/v1/sessions/{id}/details` | Get session details | ❌ No |
| POST | `/api/v1/sessions/{id}/query` | Submit query | ✅ Default (optional override) |
| GET | `/api/v1/tasks/{id}` | Get task status | ❌ No |
| POST | `/api/v1/tasks/{id}/cancel` | Cancel task | ❌ No |

**Profile Requirement Notes:**
- `POST /api/v1/sessions` requires a **default profile** (or `profile_id` in the body). The profile needs an LLM configuration; a tool_enabled (Optimize) profile also needs an MCP server; a Coordinate profile needs neither. Returns `400` if no profile is configured, `503` if it is incomplete.
- `POST /api/v1/sessions/{id}/query` uses the user's **default profile** by default. Accepts optional `profile_id` in the request body to override it for that query — send the **bare question**: never put an `@TAG` in `prompt` (the REST path does not strip it). Each query can use a different profile within the same session, and the UI displays the correct profile badge for each message.
- Both are behind the **activation firewall**: the effective profile must be activated, else `403` with `code: profile_not_activated` (or `profile_not_found`, `no_profile`).
- Poll `GET /api/v1/tasks/{id}` until `status` is `complete`, `error` or `cancelled`; a turn that answered nothing is `error`.

### RAG Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/rag/collections` | List collections |
| POST | `/api/v1/rag/collections` | Create collection |
| PUT | `/api/v1/rag/collections/{id}` | Update collection |
| DELETE | `/api/v1/rag/collections/{id}` | Delete collection |
| POST | `/api/v1/rag/collections/{id}/toggle` | Enable/disable |
| POST | `/api/v1/rag/collections/{id}/refresh` | Refresh vectors (the collection's owner or an admin; `403` otherwise) |
| POST | `/api/v1/rag/collections/{id}/populate` | Populate from template |
| GET | `/api/v1/rag/templates` | List templates |
| POST | `/api/v1/rag/generate-questions` | Generate Q&A pairs (MCP context) |
| POST | `/api/v1/rag/generate-questions-from-documents` | Generate Q&A pairs (documents) |

### HTTP Status Codes

| Code | Meaning | Common Causes |
|------|---------|---------------|
| 200 | OK | Success |
| 201 | Created | Resource created |
| 202 | Accepted | Task submitted |
| 400 | Bad Request | Invalid parameters, no profile configured |
| 401 | Unauthorized | Missing/invalid token |
| 403 | Forbidden | Insufficient permissions; `profile_not_activated` (activation firewall); `component_disabled` (Files) |
| 404 | Not Found | Resource doesn't exist |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Error | Server error |
| 503 | Service Unavailable | Profile incomplete (missing LLM or MCP) |

---

## 9. API Updates & Migration Notes

### Recent Changes (October 2026)

#### Endpoint-coverage audit (2026-10-30)
**What Changed:** A second full enumeration of the registered routes (808 method/path pairs across
23 route modules in `src/trusted_data_agent/api/`) against this document. Added: **Files and File
Stores (§3.44)**, **Agent Memory (§3.45)**, **Marketplace — collections, extensions, component
instances, sharing (§3.46)**, the **Admin Endpoint Reference incl. enterprise SSO/SAML (§3.22.2)**,
the **Prompt Editor endpoints (§3.12.5)**, **Flow Definitions (§3.32.0)**, an **Additional Endpoint Reference (§3.47)** for the
remaining families, and the **Web-UI internal routes (§3.48)**. Corrected: the LLM-configuration
test path (`POST /api/v1/llm_configurations/<id>/test`), the provenance-verify path
(`POST /v1/sessions/<id>/provenance/verify`), the KG extension-publish path
(`POST /v1/knowledge-graph/<profile_id>/publish`), the login/logout and session-creation responses,
the query firewall error codes, and the task-cancel response. The second `### 3.40` heading is now
`### 3.40a. Database Navigator`.

**Audit findings:**
- FIXED — `GET /api_key/<provider>` returned the server's environment provider keys without
  authentication; it is now admin only (§3.48).
- FIXED — `POST /api/v1/admin/session-stores/cleanup-orphans` had no admin gate and judged orphans
  against the caller's sessions; it is now admin only and judges over every user's sessions (§3.22.2).
- FIXED — `GET /api/notifications/subscribe` trusted a `user_uuid` query parameter; it now requires a
  ticket from `POST /api/notifications/ticket`.
- FIXED — `POST /api/v1/rag/collections/{id}/refresh` had no authentication; it now requires the
  collection's owner or an admin.
- FIXED — the duplicate `GET /api/v1/system-prompts/list` registration (June audit): the unreachable second handler was removed.

### Recent Changes (June 2026)

#### ➕ MCP Server + full endpoint-coverage audit
**What Changed:** Uderia now ships an **MCP server** (§3.33) that exposes profiles as governed MCP
tools. A full codebase enumeration (552 endpoints across 16 route files) was used to (a) add the
previously-undocumented endpoint families — **Agent Evaluation (§3.34)**, **Knowledge Graph
Management + KG Store (§3.35)**, **Database Connections (§3.36)**, **Platform Connectors &
Connector Registry (§3.37)**, **Component Instances & Knowledge-repository documents (§3.38)** —
and (b) produce the **[REST → MCP Tier Matrix](MCP_TIER_MATRIX.md)** mapping every endpoint to the
MCP consumer tier (`all` / `developer` / `admin` / `N/A`) at which it is exposed.

**Audit findings (tracked):**
- FIXED — the duplicate `GET /api/v1/system-prompts/list` registration: the unreachable second
  handler was removed (`list_all_prompts` always served).
- A few endpoints enforce admin via an internal `is_admin` check rather than a decorator
  (`/api/v1/auth/user/consumption-summary`, flow `toggle-platform`/`platform-meta`).
- System-prompt mutations are gated by the **PE/Enterprise license tier** (`require_prompt_engineer_or_enterprise`), distinct from the user/developer/admin role tier.

### Recent Changes (April 2026)

#### ➕ New Provider: OpenRouter
**What Changed:** OpenRouter is now a supported LLM provider (`provider: "OpenRouter"`). It routes to `https://openrouter.ai/api/v1` and uses the `openrouter_api_key` credential field. Model names follow the `provider/model-name` format (e.g., `openai/gpt-4o`, `anthropic/claude-3-5-sonnet`).

**See Also:** Section 3.16.2 for the request body example.

#### ➕ FriendliAI Dedicated Endpoint Support
**What Changed:** Friendli.AI configurations now support a `friendli_endpoint_url` credential field to route requests to a dedicated deployment instead of the shared serverless tier.

**See Also:** Section 3.16.2 for the request body examples (serverless vs. dedicated).

#### ➕ New Sections: Vector Store, Provenance, KG Marketplace
**What Changed:** Three API surface areas that were previously undocumented are now fully documented:
- **Section 3.26** — Vector Store Configuration Management (`/v1/vectorstore/*`)
- **Section 3.27** — Execution Provenance & Audit Trail (`/v1/sessions/{id}/provenance/*`)
- **Section 3.28** — Knowledge Graph Marketplace (`/v1/marketplace/knowledge-graphs/*`)

#### 🔄 Agent Pack v1.4 — Knowledge Graph Bundling
**What Changed:** Agent pack export now bundles the active Knowledge Graph for each exported profile. Import restores KGs after profiles are created, preserving the name, description, database association, and active/inactive status.

**Manifest version:** `1.4` when knowledge graphs are present (superset of 1.3). New top-level `knowledge_graphs` array; per-profile `kg_refs` field.

**Non-displacing import:** Imported KGs are only set active when the target profile has no active KG yet — an existing active KG is never displaced.

**Backward Compatibility:** ✅ v1.1, v1.2, and v1.3 packs continue to work unchanged.

#### 🔄 Agent Pack v1.3 — Skill Bundling
**What Changed:** Agent pack export now bundles user skills that are auto-enabled on any exported profile. Import installs bundled skills before creating profiles.

**Manifest version:** `1.3` when skills are present (see Section 3.14 for details).

**Backward Compatibility:** ✅ v1.1 and v1.2 packs continue to work unchanged.

#### 🔄 Skill List: Marketplace Publish Status
**What Changed:** `GET /v1/skills` now annotates each skill with `is_marketplace_listed` (bool) and `marketplace_id` (string|null) for the current user's publish status.

**Backward Compatibility:** ✅ New fields added; existing fields unchanged.

#### 🔄 Skill Marketplace Browse: Excludes Own Skills
**What Changed:** `GET /v1/marketplace/skills` no longer returns skills published by the caller. Publishers browse the marketplace from the perspective of other users.

---

### Recent Changes (November 2025)

#### 🎯 Profile Override Feature - NEW
**What Changed:** `POST /api/v1/sessions/{id}/query` now accepts optional `profile_id` parameter for per-query profile override.

**What This Means:**
- Execute different queries in the same session with different profiles
- Each query can use a different LLM/MCP combination
- UI displays the correct profile badge for each message
- Provides complete visibility into which profile was used for each query

**Usage:**
```bash
# Query with default profile (no profile_id needed)
curl -X POST http://localhost:5050/api/v1/sessions/{id}/query \
  -d '{"prompt": "Your question"}'

# Query with profile override
curl -X POST http://localhost:5050/api/v1/sessions/{id}/query \
  -d '{"prompt": "Your question", "profile_id": "profile-xxx"}'
```

**Backward Compatibility:** ✅ Fully backward compatible. Existing code without `profile_id` will continue to work using the default profile.

**See Also:** Section 3.5.1 "Submit a Query" for complete details and examples.

#### 🔄 User-Scoped Sessions & Queries
**What Changed:** Sessions and queries are now fully user-scoped through JWT/access token authentication.

**Before:**
- Sessions could be created with just an Authorization header
- Optional `X-TDA-User-UUID` header to specify user (or default to system UUID)
- User isolation was not enforced at API level

**Now:**
- User identity is **always** extracted from the authentication token
- No custom headers needed - the system automatically associates all resources with the authenticated user
- 404 responses if you try to access another user's sessions
- **Migration:** Remove any `X-TDA-User-UUID` header usage from your clients

#### ✅ Unified Endpoint Paths
**What Changed:** All API endpoints now use consistent `/api/v1/` prefix.

**Impact:**
- Consistent path structure across all endpoints
- Documentation updated to reflect correct paths
- Both styles work during transition period, but use `/api/v1/` for new code

#### 🔐 Authentication Requirements
**What Changed:** All data-modifying operations now require authentication.

**Current Requirements:**
- `GET /api/v1/rag/collections` - Public (no auth)
- `POST/PUT/DELETE` operations - Require JWT or access token
- Session/query operations - Require authentication

### Migration Guide

**If you were using X-TDA-User-UUID header:**

Before:
```bash
curl -X GET http://localhost:5050/api/v1/sessions \
  -H "X-TDA-User-UUID: user-uuid-here" \
  -H "Authorization: Bearer $TOKEN"
```

After:
```bash
curl -X GET http://localhost:5050/api/v1/sessions \
  -H "Authorization: Bearer $TOKEN"
```

The user UUID is now automatically extracted from your token. Remove the `X-TDA-User-UUID` header.

---

## 10. Additional Resources

- **Planner Repository Constructors:** `docs/RAG_Templates/README.md`
- **Authentication Migration:** `docs/AUTH_ONLY_MIGRATION.md`
- **Sample Configurations:** `docs/RestAPI/scripts/sample_configs/`
- **Example Scripts:** `docs/RestAPI/scripts/`
- **Main Documentation:** `README.md`

---

**Last Updated:** May 18, 2026 (v1.9)
**API Version:** v1
**Document Version:** 2.4.0

#### `POST /v1/knowledge-graph/declare-concept` — declare a business concept (Sep 2026)

Declares, on a graph the caller owns, a business term a data product never defined — as a `business_concept` (term, definition, predicate, columns), a **compilable `metric`** (`SUM(CASE WHEN <predicate> THEN 1 ELSE 0 END)` over the entity's deployed table, consumed by `compile_metric`) and a `query_recipe`, linked to the entity and to every column the predicate names. Deterministic, product-neutral, fails closed.

Body: `kg_id`, `concept_name`, `definition`, `predicate` (ONE boolean SQL condition over the entity's declared columns — no statements), `entity` (name or leaf), optional `dimensions` `[{name, expr}]`.

Response `200`: `{status, concept, entity, metric, recipe, links, memory_module_sql}` — `memory_module_sql` holds the product's Memory-module INSERT statements (glossary, cookbook) as **text** for the owner to apply; the platform never writes to a product's database. `422` with the reason when the predicate does not parse or names a column the entity does not declare (named). Declaring changes the ontology's content hash: the trust seal reads *changed* until the owner re-signs.

Same act from an agent: `TDA_KnowledgeGraph action="declare_concept"` with `concept_name`, `definition`, `predicate`, `entity`.
