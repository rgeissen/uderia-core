# OAuth Architecture & Flow Diagrams

> **Status (2026-10-30):** code complete (Phases 1–4). The configuration/email/testing/production rows in *Current Implementation Status* below are a per-deployment checklist, not platform work. Enterprise OIDC/SAML SSO is a separate subsystem configured under Administration → SSO (see [README.md](./README.md)).

Understanding how OAuth works in Uderia.

---

## 🔄 Complete OAuth Flow

### User Browser to Your App to Provider

```
USER BROWSER                YOUR APP              PROVIDER (Google/GitHub/etc)
     │                         │                         │
     │─ Click OAuth button ───→ │                         │
     │                    (e.g., "Sign in with Google")   │
     │                         │                          │
     │                    ┌────────────────────────┐      │
     │                    │ Generate unique state  │      │
     │                    │ value for CSRF         │      │
     │                    │ Store in session       │      │
     │                    └────────────────────────┘      │
     │                         │                          │
     │◄────────Redirect────────│──────────────────────────→│
     │    to provider's   (client_id, redirect_uri,       │
     │    login page      scope, state)                    │
     │                         │                          │
     │  ┌──────────────────────────┐                      │
     │  │ User enters email/        │                      │
     │  │ password & 2FA (optional) │                      │
     │  └──────────────────────────┘                      │
     │                         │                          │
     ├─ Approves consent   ───────────────────────────────→│
     │ (user authorizes          │                        │
     │  app to access profile)    │                    ┌───────────────┐
     │                         │  │                    │ Provider      │
     │                         │  │                    │ authenticates │
     │                         │  │                    │ user          │
     │                         │  │                    └───────────────┘
     │                         │  │                        │
     │◄──────────Redirect──────────────────────────────────│
     │ with auth code        (code, state)                 │
     │ & state value                                        │
     │                         │                          │
     │                    ┌────────────────────────┐      │
     │                    │ 1. Verify state       │      │
     │                    │    matches (CSRF)     │      │
     │                    │ 2. Exchange code for  │      │
     │                    │    access token       │      │
     │                    │ 3. Get user profile   │      │
     │                    │ 4. Create/update user │      │
     │                    │ 5. Generate JWT       │      │
     │                    └────────────────────────┘      │
     │                         │                          │
     │                         │──Server-to-server call──→│
     │                    (hidden from user browser)      │
     │                         │                          │
     │                         │◄───Access token──────────│
     │                         │                          │
     │                         │──Get user info ────────→│
     │                         │                          │
     │                         │◄───User data──────────── │
     │                         │                          │
     │◄────JWT in URL/Cookie───│                         │
     │                         │                         │
     ├─ Store JWT              │                         │
     │  in localStorage         │                         │
     │  or session cookie       │                         │
     │                         │                         │
     └─ Logged in! ✅          │                         │
```

---

## 📝 OAuth Configuration Flow (Your Current Step)

```
┌─────────────────────────────────────────────────────────────┐
│                                                              │
│  STEP 1: CREATE OAUTH APPLICATION                           │
│  ├─ Visit provider's developer dashboard                    │
│  │  (Google Cloud Console, GitHub, etc.)                    │
│  ├─ Create new "OAuth application" or "OAuth App"           │
│  ├─ Register Authorized Redirect URI:                       │
│  │  http://localhost:5050/api/v1/auth/oauth/               │
│  │                         {provider}/callback              │
│  └─ Copy Client ID & Client Secret                         │
│                                                              │
│  STEP 2: POPULATE .env FILE                                │
│  ├─ Open .env in project root                              │
│  ├─ Add: OAUTH_GOOGLE_CLIENT_ID=xxx                        │
│  ├─ Add: OAUTH_GOOGLE_CLIENT_SECRET=xxx                    │
│  └─ Repeat for each provider you want to support            │
│                                                              │
│  STEP 3: CONFIGURE SETTINGS                                │
│  ├─ For Development:                                        │
│  │  OAUTH_HTTPS_ONLY=False                                 │
│  │  OAUTH_INSECURE_TRANSPORT=True                          │
│  │  OAUTH_CALLBACK_URL=http://localhost:5050/...           │
│  │                                                           │
│  └─ For Production:                                         │
│     OAUTH_HTTPS_ONLY=True                                  │
│     OAUTH_INSECURE_TRANSPORT=False                         │
│     OAUTH_CALLBACK_URL=https://yourdomain.com/...          │
│                                                              │
│  STEP 4: VERIFY CONFIGURATION                              │
│  └─ Run: bash test/verify_oauth_config.sh                           │
│     All providers should show ✅ Configured                 │
│                                                              │
│  STEP 5: TEST                                              │
│  ├─ Start: python -m trusted_data_agent                    │
│  ├─ Visit: http://localhost:5050/login                     │
│  ├─ Click provider button                                   │
│  └─ Complete OAuth flow                                     │
│                                                              │
│  STEP 6: SUCCESS ✅                                        │
│  └─ You should be logged in to your app                     │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔐 Security in OAuth

### CSRF Protection via State Parameter

```
CSRF Attack Scenario:
┌─────────────────────────────────────────────────┐
│                                                  │
│ 1. User visits malicious website                │
│ 2. Malicious site has button:                   │
│    <a href="your-site/oauth/callback?...">     │
│ 3. If no state validation, user could be        │
│    logged into attacker's account               │
│                                                  │
│ How Your App Prevents This:                     │
│ ├─ Generate random state value                  │
│ ├─ Store state in session                       │
│ ├─ Send state to provider                       │
│ ├─ Provider returns state unchanged              │
│ ├─ Verify returned state == stored state        │
│ ├─ If mismatch → Attack blocked! ✅             │
│ └─ If match → Valid request ✅                  │
│                                                  │
└─────────────────────────────────────────────────┘
```

### Client Secret Security

```
Visible to Browser:
├─ Authorization code (code parameter)
├─ State value (state parameter)
└─ User profile data in JWT

Hidden from Browser:
├─ Client Secret (never sent to browser)
├─ Access token (kept on server)
├─ User database records
└─ Audit logs

Why?
└─ Secrets stay on server
   Browser can never compromise them
   Even if browser is compromised
```

---

## 📊 Request/Response Details

### 1. Initiate OAuth Login

**User Request:**
```
GET /api/v1/auth/oauth/google HTTP/1.1
```

**Your App Response:**
```
HTTP/1.1 302 Found
Location: https://accounts.google.com/o/oauth2/v2/auth?
  client_id=YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com
  redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fapi%2Fv1%2Fauth%2Foauth%2Fgoogle%2Fcallback
  scope=openid%20email%20profile
  response_type=code
  state=random_unique_identifier_12345
```

### 2. Provider Redirects Back

**Provider Request (Browser Redirects):**
```
GET /api/v1/auth/oauth/google/callback?
  code=authorization_code_here_abcd1234
  state=random_unique_identifier_12345
  HTTP/1.1
```

**Your App Validates:**
```
1. Verify state == stored state
   If not → Reject request (CSRF attack)
   
2. Use code to get access token (server-to-server)
   Request to: https://oauth2.googleapis.com/token
   With: code, client_id, client_secret
   
3. Get user info from provider
   Request to: https://www.googleapis.com/oauth2/v1/userinfo
   With: access_token
   
4. Response contains:
   {
     "email": "user@example.com",
     "name": "John Doe",
     "picture": "https://...",
     ...
   }
```

### 3. Your App Creates Session

**Your App:**
```
1. Check if user exists
   SELECT * FROM users WHERE oauth_id = "google:1234567890"
   
2. If not exists:
   INSERT INTO users (oauth_id, email, name, ...)
   
3. If exists:
   UPDATE users SET last_login = NOW()
   
4. Generate JWT token:
   {
     "user_id": 123,
     "email": "user@example.com",
     "iat": 1234567890,
     "exp": 1234571490
   }
   
5. Respond with JWT
```

**Response to Browser:**
```
HTTP/1.1 302 Found
Location: http://localhost:5050/?token=JWT_TOKEN_HERE

Set-Cookie: session=...; Secure; HttpOnly; SameSite=Lax
```

### 4. Browser Stores Token

**Browser JavaScript:**
```javascript
// From URL or cookie
localStorage.setItem('jwt_token', token);

// Include in future requests
Authorization: Bearer JWT_TOKEN
```

---

## 🗂️ Project File Organization

```
<project root>/
│
├── .env                                    ← Your credentials (PRIVATE)
├── .env.oauth.template                    ← Reference template
├── test/verify_oauth_config.sh            ← Verification script
│
├── src/trusted_data_agent/auth/
│   ├── oauth_config.py                    ← Phase 1: Provider config
│   ├── oauth_handlers.py                  ← Phase 2: OAuth flow
│   ├── oauth_middleware.py                ← Phase 2: Quart integration
│   ├── email_verification.py              ← Phase 4: Email verification
│   ├── account_merge.py                   ← Phase 4: Account merging
│   ├── oauth_rate_limiter.py              ← Phase 4: Rate limiting
│   ├── oauth_audit_logger.py              ← Phase 4: Audit logging
│   └── models.py                          ← Database models
│
├── src/trusted_data_agent/api/
│   └── auth_routes.py                     ← OAuth API endpoints
│
├── static/js/
│   ├── oauth.js                           ← Phase 3: OAuth client
│   ├── connected-accounts.js              ← Phase 3: Account management
│   └── auth.js                            ← Phase 3: Auth handling
│
├── templates/
│   └── login.html                         ← Phase 3: Login UI
│
└── docs/OAuth/
    ├── README.md                          ← Index & navigation
    ├── GETTING_STARTED.md                 ← Quick start
    ├── SETUP_GUIDE.md                     ← Provider credential setup
    ├── CONFIGURATION.md                   ← Configuration & verification
    ├── ARCHITECTURE.md                    ← This file (flows & diagrams)
    ├── INTEGRATION_GUIDE.md               ← Code integration
    ├── QUICK_REFERENCE.md                 ← API reference
    └── SECURITY.md                        ← Security features
```

---

## 📱 API Endpoints

### Authentication Routes
```
POST   /api/v1/auth/login                  User/password login
POST   /api/v1/auth/register               Create account
POST   /api/v1/auth/logout                 Logout
POST   /api/v1/auth/refresh                Refresh token
```

### OAuth Routes
```
GET    /api/v1/auth/oauth/providers        List available providers
GET    /api/v1/auth/oauth/<provider>       Initiate OAuth login
GET    /api/v1/auth/oauth/<provider>/callback    Handle callback
GET    /api/v1/auth/oauth/<provider>/link       Initiate account link
GET    /api/v1/auth/oauth/<provider>/link/callback Handle link callback
POST   /api/v1/auth/oauth/<provider>/disconnect Unlink account
GET    /api/v1/auth/oauth/accounts        List user's linked accounts
```

### Email Verification Routes
```
POST   /api/v1/auth/resend-verification-email  Send verification email
POST   /api/v1/auth/verify-email              Verify email token
```

---

## 🔄 Current Implementation Status

```
PHASE 1: Foundation ✅ COMPLETE
├─ Dependencies added (authlib, httpx)
├─ Database models created
└─ Configuration created

PHASE 2: Authlib Integration ✅ COMPLETE
├─ OAuth handlers implemented
├─ OAuth middleware created
└─ API routes added (7 endpoints)

PHASE 3: Frontend Integration ✅ COMPLETE
├─ Login UI updated
├─ JavaScript clients created
└─ Documentation written

PHASE 4: Security & Polish ✅ COMPLETE
├─ Email verification service
├─ Account merging service
├─ Rate limiting service
└─ Audit logging service

CONFIGURATION: 🔄 IN PROGRESS
├─ Get provider credentials (you are here)
├─ Fill .env file
├─ Verify configuration
└─ Test in browser

EMAIL SETUP: ⏳ PENDING
├─ Configure SMTP/SendGrid/AWS SES
└─ Enable email verification

TESTING: ⏳ PENDING
├─ Test OAuth flow for each provider
├─ Test email verification
├─ Test rate limiting
└─ Test account linking

PRODUCTION: ⏳ PENDING
├─ Update .env for production
├─ Configure Redis for rate limiting
├─ Set up monitoring/alerting
└─ Deploy to server
```

---

## 🎯 Data Flow Summary

```
1. USER INITIATES LOGIN
   ├─ Clicks "Sign in with Google"
   └─ Browser → /api/v1/auth/oauth/google

2. YOUR APP PREPARES OAUTH
   ├─ Generate state (CSRF token)
   ├─ PKCE S256 verifier/challenge (google, github, microsoft, okta;
   │  TDA_OAUTH_PKCE=0 disables)
   ├─ Store state + verifier in session
   └─ Redirect to provider with state + code_challenge

3. PROVIDER AUTHENTICATES USER
   ├─ User logs in
   ├─ User approves consent
   └─ Provider redirects to your callback URL

4. YOUR APP RECEIVES CALLBACK
   ├─ Verify state parameter
   ├─ Exchange code (+ PKCE verifier) for access token
   ├─ Get user info from provider
   └─ Create JWT token for user

5. USER IS LOGGED IN
   ├─ Browser stores JWT
   ├─ JWT included in API requests
   ├─ Your app validates JWT
   └─ User has full access

6. ONGOING REQUESTS
   ├─ Browser includes JWT header
   ├─ Authorization: Bearer JWT_TOKEN
   └─ Your app validates and processes request
```

---

## 🔗 Next Steps

1. **Understand Configuration** → [CONFIGURATION.md](./CONFIGURATION.md)
2. **Get Provider Credentials** → [SETUP_GUIDE.md](./SETUP_GUIDE.md)
3. **Fill .env & Test** → [CONFIGURATION.md](./CONFIGURATION.md)
4. **Integrate into Code** → [INTEGRATION_GUIDE.md](./INTEGRATION_GUIDE.md)
5. **Set Up Security** → [SECURITY.md](./SECURITY.md)

---

**Back to:** [README.md](./README.md) | [GETTING_STARTED.md](./GETTING_STARTED.md)
