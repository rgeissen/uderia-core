# Profile Override Feature for REST API

*Last verified against the code: 2026-10-30. Full reference: [restAPI.md §3.5.1](restAPI.md#351-submit-a-query).*

## Overview

The REST API now supports optional **profile overrides** for individual queries. This allows you to:

- Execute queries with different profiles from the same session
- Have each query display with its own profile badge in the UI
- Maintain full visibility into which profile was used for each query

## Usage

### 1. Basic Query (Uses Default Profile)

```bash
curl -X POST http://localhost:5050/api/v1/sessions/{session_id}/query \
  -H "Authorization: Bearer {jwt_token}" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is the system version?"}'
```

**Result:**
- Query executes with the user's default profile
- Message displays with default profile badge (e.g., `@GOGET`)

### 2. Query with Profile Override

```bash
curl -X POST http://localhost:5050/api/v1/sessions/{session_id}/query \
  -H "Authorization: Bearer {jwt_token}" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Tell me about the current date",
    "profile_id": "profile-1764006444002-z0hdduce9"
  }'
```

**Result:**
- Query executes with the specified profile
- Message displays with the override profile badge (e.g., `@FRGOT`)
- Session shows both profiles in `profile_tags_used`

### Do NOT put the `@TAG` in the prompt

`profile_id` alone selects the profile. The `@TAG` syntax is a **web-UI-only** convenience: the UI
strips the leading tag before sending (`static/js/eventHandlers.js`). The REST path
(`POST /v1/sessions/{id}/query` → `rest_routes._start_query_task`) does **not** strip it, so a tag left
in the prompt is stored verbatim as the user query and then:

- changes the retrieval embedding, so a different RAG / knowledge chunk set comes back (this silently
  invalidates an A/B comparison between profiles);
- renders in the chat log as a duplicate of the profile badge.

Send the bare question:

```bash
# Correct
-d '{"prompt": "How many databases are on the system?", "profile_id": "profile-1764006444002-z0hdduce9"}'

# Wrong — the tag becomes part of the query text
-d '{"prompt": "@OPTIM How many databases are on the system?", "profile_id": "profile-1764006444002-z0hdduce9"}'
```

### The profile must be activated

Both the default profile and any override pass the REST **activation firewall**
(`rest_routes._assert_profile_activated`): the profile must be in the user's
`active_for_consumption_profile_ids` (set in Anatomy → Agent Profiles, or
`POST /v1/profiles/set_active_for_consumption`; activation requires all of the profile's tests to
pass). Coordinate (genie) child sessions are exempt — the coordinator drives its validated children
in-process.

## How It Works

### Backend Flow

1. **REST Request** → Include optional `profile_id` in request body
2. **REST Endpoint** → Extracts `profile_id` (or uses default), then runs the activation firewall on the effective profile (`403` if it fails)
3. **Execution Service** → `run_agent_execution(..., profile_override_id=...)`
4. **Profile Context** → Switches to the requested profile
5. **Message Storage** → Stores the profile tag with the user message
6. **Session History** → Each message remembers which profile executed it

### Frontend Display

When loading a session:
1. UI reads `profile_tag` from each message
2. For user messages with a profile_tag, renders a profile badge
3. Badge shows profile name and color (from profile configuration)
4. Different messages can show different profile badges

## Getting Available Profiles

To see available profiles and get their IDs:

```bash
curl -X GET http://localhost:5050/api/v1/profiles \
  -H "Authorization: Bearer {jwt_token}"
```

Response:
```json
{
  "profiles": [
    {
      "id": "profile-1763993711628-vvbh23q09",
      "tag": "GOGET",
      "name": "Google - Reduced Stack",
      "color": "#4285f4",
      ...
    },
    {
      "id": "profile-1764006444002-z0hdduce9",
      "tag": "FRGOT",
      "name": "Friendly AI - Reduced Stack",
      "color": "#ff6b6b",
      ...
    }
  ],
  "default_profile_id": "profile-1763993711628-vvbh23q09"
}
```

## Examples

### Python

```python
import requests
import json

BASE_URL = "http://localhost:5050"
API_BASE = f"{BASE_URL}/api/v1"

# Login
login_response = requests.post(
    f"{BASE_URL}/api/v1/auth/login",
    json={"username": "admin", "password": "admin"}
)
jwt_token = login_response.json()['token']

# Get profiles
profiles_response = requests.get(
    f"{API_BASE}/profiles",
    headers={"Authorization": f"Bearer {jwt_token}"}
)
profiles = profiles_response.json()['profiles']
override_profile_id = profiles[1]['id']  # Use second profile

# Create session
session_response = requests.post(
    f"{API_BASE}/sessions",
    headers={"Authorization": f"Bearer {jwt_token}"}
)
session_id = session_response.json()['session_id']

# Query with default profile
query1 = requests.post(
    f"{API_BASE}/sessions/{session_id}/query",
    headers={"Authorization": f"Bearer {jwt_token}"},
    json={"prompt": "What is the system version?"}
)

# Query with profile override
query2 = requests.post(
    f"{API_BASE}/sessions/{session_id}/query",
    headers={"Authorization": f"Bearer {jwt_token}"},
    json={
        "prompt": "Tell me about the current date",
        "profile_id": override_profile_id
    }
)
```

### Node.js/JavaScript

```javascript
const baseUrl = 'http://localhost:5050';
const apiBase = `${baseUrl}/api/v1`;

// Login
const loginRes = await fetch(`${baseUrl}/api/v1/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'admin', password: 'admin' })
});
const { token } = await loginRes.json();

// Get profiles
const profilesRes = await fetch(`${apiBase}/profiles`, {
  headers: { 'Authorization': `Bearer ${token}` }
});
const { profiles } = await profilesRes.json();

// Create session
const sessionRes = await fetch(`${apiBase}/sessions`, {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${token}` }
});
const { session_id } = await sessionRes.json();

// Query with profile override
const queryRes = await fetch(`${apiBase}/sessions/${session_id}/query`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    prompt: 'Tell me about the current date',
    profile_id: profiles[1].id  // Override with second profile
  })
});
const { task_id } = await queryRes.json();
```

## UI Display

When viewing a session with multiple profiles:

```
┌─────────────────────────────────────────────────────────┐
│ User (12:34 PM)                                         │
│ @GOGET (default)                                        │
│                                                         │
│ What is the system version?                             │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Assistant                                               │
│                                                         │
│ The system version is 20.00.22.31.                      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ User (12:35 PM)                                         │
│ @FRGOT (overridden)                                     │
│                                                         │
│ Tell me about the current date                          │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Assistant                                               │
│                                                         │
│ The current date is 2025-11-26.                         │
└─────────────────────────────────────────────────────────┘
```

Each profile badge shows:
- **Tag** (e.g., `@GOGET`, `@FRGOT`)
- **Color** from the profile's branding
- **Tooltip** on hover showing full profile name

## Error Handling

If you specify an invalid `profile_id`:

```bash
curl -X POST http://localhost:5050/api/v1/sessions/{session_id}/query \
  -H "Authorization: Bearer {jwt_token}" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Test", "profile_id": "invalid-id"}'
```

**Response:** `403 Forbidden` from the activation firewall — the query is not started:

```json
{"error": "Profile 'invalid-id' not found.", "code": "profile_not_found", "profile_id": "invalid-id"}
```

The other codes are `profile_not_activated` (the profile exists but is not activated — the body adds
`profile_tag`), `no_profile` (no override and no default) and `activation_check_failed` (the check
itself failed; the firewall fails closed). A successful submission answers `202` with
`{task_id, status_url}`; poll `GET /api/v1/tasks/{task_id}` until `status` is `complete`, `error` or
`cancelled`.

## Session Details

You can check which profiles were used in a session:

```bash
curl -X GET http://localhost:5050/api/v1/sessions/{session_id}/details \
  -H "Authorization: Bearer {jwt_token}"
```

Response includes:
```json
{
  "session_id": "87ebb982-5e22-4307-b7a2-fb079ee27fa0",
  "profile_id": "profile-1763993711628-vvbh23q09",
  "profile_tags_used": ["GOGET", "FRGOT"],
  "models_used": [
    "Google/gemini-2.5-flash",
    "Friendli/meta-llama/Llama-3.3-70B-Instruct"
  ],
  ...
}
```

## Implementation Details

### Files Modified

- `src/trusted_data_agent/api/rest_routes.py`
  - POST `/v1/sessions/{id}/query` endpoint (`execute_query` → `_start_query_task`)
  - Accepts optional `profile_id` in request body
  - Enforces the activation firewall (`_assert_profile_activated`) on the effective profile
  - Passes `profile_override_id` to execution service
  - Stores `profile_id_override` in task state

- `src/trusted_data_agent/agent/execution_service.py`
  - Receives `profile_override_id` parameter
  - Resolves profile tag from profile ID
  - Passes profile tag to `add_message_to_histories()`

- `src/trusted_data_agent/core/session_manager.py`
  - `add_message_to_histories()` already supported `profile_tag`
  - Messages now store profile information

- `static/js/handlers/sessionManagement.js`
  - Reads `profile_tag` from loaded messages
  - Passes it to UI.addMessage()

- `static/js/ui.js`
  - `addMessage()` creates profile badges for messages with `profile_tag`
  - Applies profile-specific styling (color, branding)

### Message Structure

```json
{
  "role": "user",
  "content": "What is the system version?",
  "profile_tag": "GOGET",
  "source": "rest",
  "turn_number": 1,
  "isValid": true
}
```

## Testing

Run the provided test scripts to verify the feature:

```bash
# Test basic profile override functionality
python test/test_profile_override.py

# Test per-message profile tracking
python test/test_profile_override_per_message.py
```

## Notes

- Profile overrides are **per-query**, not per-session (a `tool_enabled` override runs on a per-executor state overlay — it never mutates the process-global state, so concurrent turns cannot see each other's override)
- Never send the `@TAG` in the prompt text on REST — `profile_id` alone selects the profile
- Sessions maintain their primary profile for branding purposes
- Each message independently records which profile executed it
- The UI displays the correct profile badge for each message
- Profile colors and styling come from profile configuration

