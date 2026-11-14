# Consumption Profiles Implementation

**Last verified against the code:** 2026-10-30

## Overview

The consumption profiles feature allows administrators to manage user quotas and rate limits through reusable profile templates. Users can be assigned to profiles that define their monthly token limits and rate limiting parameters.

## Rate Limiting & Consumption Profile Precedence

**How enforcement is decided today.** Consumption profile limits are enforced for every
**non-admin** user by `ConsumptionEnforcer.can_execute_prompt()` (`auth/consumption_enforcer.py`)
before `/ask_stream`, `/simple_chat` and `/invoke_prompt_stream`, and every turn is also checked
by `ConsumptionManager.check_rate_limits()` / `check_token_quota()` at the start of
`PlanExecutor.run()` (which covers the REST query path). Neither reads the **Enable Rate Limiting**
switch: that switch (`rate_limit_enabled` in `system_settings`) governs the token-bucket limiter in
`auth/rate_limiter.py` — in practice the per-IP login/registration limits.
Admins bypass the consumption check. Both checks fail **open** on an unexpected error (logged; the
executor counts bypasses in `APP_STATE['consumption_enforcement_bypasses']`, and
`core/quota_health.report_bypass` logs CRITICAL and banners connected admins — first bypass, then at
most every 15 minutes).

### Precedence Hierarchy (for authenticated users, `ConsumptionEnforcer._load_user_and_profile`):

1. **Global Override Mode** (Emergency) - When `rate_limit_global_override` is on, the global per-user prompt/config limits apply to ALL users, ignoring consumption profiles (no token limits in this mode)
2. **Assigned Consumption Profile** - The user's `consumption_profile_id`
3. **Default Profile** - The profile marked `is_default` when the user has none assigned
4. **Unlimited** - When no profile can be found at all

### Key Settings (Administration → App Config, rate-limit section):

- **Enable Rate Limiting** - Master switch for the token-bucket limiter (IP login/registration limits); seeded from `tda_config.json` `rate_limit_enabled` (default on)
- **Global Override Mode** - Emergency toggle to override all profiles with global limits (disabled by default)
- **Per-User Limits** - The global limits used in override mode (`rate_limit_user_prompts_per_hour` 100, `…_per_day` 1000, `…_configs_per_hour` 10)
- **Per-IP Limits** - Login (5/min) and registration (3/hour), enforced in `auth_routes.py` for unauthenticated traffic (not affected by profiles); an API limit (60/min) is stored but no route applies it

## Configuration

You can configure the bootstrap defaults in `tda_config.json` (read once to seed `system_settings` and the `consumption_profiles` table; later changes are made in the UI):

```json
{
  "rate_limit_enabled": "on",
  "default_consumption_profile": "Unlimited"
}
```

**Configuration Parameters:**

### `rate_limit_enabled`
Controls whether rate limiting is enabled system-wide.

**Supported Values:**
- `"on"` / `"true"` / `"1"` / `"yes"` - Enable the rate limiter **[Default]**
- `"off"` / `"false"` / `"0"` / `"no"` - Disable the rate limiter (not recommended for production)

Consumption profile limits are enforced independently of this switch (see above).

### `default_consumption_profile`
Determines which profile is assigned to new users.

**Supported Values:**
- `"Free"` - Basic limits (50 prompts/hour, 100K input tokens/month)
- `"Pro"` - Professional limits (200 prompts/hour, 500K input tokens/month)
- `"Enterprise"` - High limits (500 prompts/hour, 2M input tokens/month)
- `"Unlimited"` - No token limits (1000 prompts/hour, unlimited tokens) **[Default]**

This setting determines which profile is marked as `is_default=True` when the profiles are bootstrapped at startup (`auth/database.py` → `_bootstrap_consumption_profiles`, from the `consumption_profiles` array in `tda_config.json`); a user with no assigned profile is enforced against the default profile.

## Implementation Status

✅ **Completed Components:**

1. **Database Models** (`src/trusted_data_agent/auth/models.py`)
   - `ConsumptionProfile` - Profile definitions
   - `UserTokenUsage` - Monthly token consumption tracking
   - `User.consumption_profile_id` - Profile assignment

2. **Token Quota Management** (`src/trusted_data_agent/auth/token_quota.py`)
   - `get_user_consumption_profile()` - Fetch user's profile
   - `check_token_quota()` - Validate token usage against limits
   - `record_token_usage()` - Track token consumption
   - `get_user_quota_status()` - Get comprehensive quota status

3. **Enforcement** (`src/trusted_data_agent/auth/consumption_enforcer.py`, `auth/consumption_manager.py`)
   - `ConsumptionEnforcer.can_execute_prompt()` - hourly/daily prompt limits (one query) and monthly input/output token limits from `user_token_usage`, before the streaming endpoints
   - `ConsumptionManager.check_rate_limits()` / `check_token_quota()` - checked at the start of every executor turn against `user_consumption`
   - `GET /api/v1/consumption_warnings` - the warning level shown when a user approaches a limit
   - (`rate_limiter.check_user_prompt_quota()` / `check_user_config_quota()` still exist but are not on the prompt path)

4. **Admin API Endpoints** (`src/trusted_data_agent/api/auth_routes.py`)
   - `GET /api/v1/auth/admin/consumption-profiles` - List all profiles
   - `POST /api/v1/auth/admin/consumption-profiles` - Create profile
   - `PUT /api/v1/auth/admin/consumption-profiles/<id>` - Update profile
   - `DELETE /api/v1/auth/admin/consumption-profiles/<id>` - Delete profile
   - `PUT /api/v1/auth/admin/users/<id>/consumption-profile` - Assign profile to user
   - `GET /api/v1/auth/user/quota-status` - Get current user's quota status
   - `GET /api/v1/auth/user/consumption-summary` - Current user's consumption summary

5. **Startup Bootstrap** (`src/trusted_data_agent/auth/database.py`)
   - Tables created at startup (the one-time `maintenance/migrate_consumption_profiles.py` was removed)
   - `_bootstrap_consumption_profiles()` creates the profiles listed in `tda_config.json` (Free, Pro, Enterprise, Unlimited) if missing

6. **Token Tracking** - `session_manager.update_token_count()` calls `token_quota.record_token_usage()` for every token update (monthly `user_token_usage`), alongside the `user_consumption` ledger (see [CONSUMPTION_TRACKING_IMPLEMENTATION.md](CONSUMPTION_TRACKING_IMPLEMENTATION.md))

7. **UI** - Administration → Consumption Profiles tab (list, create/edit/delete, default, active), the user list's profile assignment (`static/js/adminManager.js`), and the user's own quota display (`static/js/handlers/userProfileHandler.js`, reads `quota-status`)

## Database Schema

### consumption_profiles
```sql
CREATE TABLE consumption_profiles (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    prompts_per_hour INTEGER NOT NULL DEFAULT 100,
    prompts_per_day INTEGER NOT NULL DEFAULT 1000,
    config_changes_per_hour INTEGER NOT NULL DEFAULT 10,
    input_tokens_per_month INTEGER,  -- NULL = unlimited
    output_tokens_per_month INTEGER,  -- NULL = unlimited
    is_default BOOLEAN DEFAULT 0,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

### user_token_usage
```sql
CREATE TABLE user_token_usage (
    id INTEGER PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    period VARCHAR(7) NOT NULL,  -- YYYY-MM format
    input_tokens_used INTEGER DEFAULT 0,
    output_tokens_used INTEGER DEFAULT 0,
    total_tokens_used INTEGER DEFAULT 0,
    first_usage_at TIMESTAMP,
    last_usage_at TIMESTAMP,
    UNIQUE(user_id, period)
);
```

### users (modified)
```sql
ALTER TABLE users ADD COLUMN consumption_profile_id INTEGER;
```

## Default Profiles

`Unlimited` is the default profile unless `default_consumption_profile` says otherwise.

### 1. Free
- Prompts: 50/hour, 500/day
- Config changes: 5/hour
- Input tokens: 100K/month
- Output tokens: 50K/month

### 2. Pro
- Prompts: 200/hour, 2000/day
- Config changes: 20/hour
- Input tokens: 500K/month
- Output tokens: 250K/month

### 3. Enterprise
- Prompts: 500/hour, 5000/day
- Config changes: 50/hour
- Input tokens: 2M/month
- Output tokens: 1M/month

### 4. Unlimited (Default)
- Prompts: 1000/hour, 10000/day
- Config changes: 100/hour
- Input tokens: Unlimited
- Output tokens: Unlimited

## Setup

### 1. Start the Application

No migration step is needed: the tables are created and the profiles from `tda_config.json` are
bootstrapped on startup.

### 2. (Optional) Assign Unlimited to Existing Users

```bash
python maintenance/assign_unlimited_profiles.py
```

Assigns the Unlimited profile to all existing users and marks it as the default for new users.

### 3. Verify Installation

```bash
# Test that default profiles exist
sqlite3 tda_auth.db "SELECT * FROM consumption_profiles;"

# Verify users' assigned profiles
sqlite3 tda_auth.db "SELECT username, consumption_profile_id FROM users;"
```

## API Examples

### Get All Profiles (Admin)
```bash
curl -H "Authorization: Bearer <admin_token>" \
  http://localhost:5050/api/v1/auth/admin/consumption-profiles
```

### Create Profile (Admin)
```bash
curl -X POST -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Starter",
    "description": "Starter tier",
    "prompts_per_hour": 30,
    "prompts_per_day": 300,
    "config_changes_per_hour": 3,
    "input_tokens_per_month": 50000,
    "output_tokens_per_month": 25000,
    "is_default": false
  }' \
  http://localhost:5050/api/v1/auth/admin/consumption-profiles
```

### Assign Profile to User (Admin)
```bash
curl -X PUT -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"profile_id": 2}' \
  http://localhost:5050/api/v1/auth/admin/users/<user_id>/consumption-profile
```

### Get My Quota Status (User)
```bash
curl -H "Authorization: Bearer <user_token>" \
  http://localhost:5050/api/v1/auth/user/quota-status
```

Response:
```json
{
  "status": "success",
  "quota": {
    "has_quota": true,
    "period": "2025-12",
    "profile_name": "Pro",
    "profile_id": 2,
    "input_tokens": {
      "limit": 500000,
      "used": 45230,
      "remaining": 454770,
      "percentage_used": 9.0
    },
    "output_tokens": {
      "limit": 250000,
      "used": 23150,
      "remaining": 226850,
      "percentage_used": 9.3
    },
    "rate_limits": {
      "prompts_per_hour": 200,
      "prompts_per_day": 2000,
      "config_changes_per_hour": 20
    }
  }
}
```

## Testing

### Test Profile Creation
```python
# In Python/IPython
from trusted_data_agent.auth.database import get_db_session
from trusted_data_agent.auth.models import ConsumptionProfile

with get_db_session() as session:
    profiles = session.query(ConsumptionProfile).all()
    for p in profiles:
        print(f"{p.id}: {p.name} - {p.input_tokens_per_month}/{p.output_tokens_per_month}")
```

### Test Token Tracking
```python
from trusted_data_agent.auth.token_quota import (
    check_token_quota,
    record_token_usage,
    get_user_quota_status
)

user_id = "your-user-id"

# Record some usage
record_token_usage(user_id, input_tokens=1000, output_tokens=500)

# Check quota
allowed, msg, info = check_token_quota(user_id, input_tokens=5000, output_tokens=2500)
print(f"Allowed: {allowed}, Message: {msg}")

# Get status
status = get_user_quota_status(user_id)
print(status)
```

## Benefits

1. **Flexible Quota Management**: Create profiles for different user tiers
2. **Token Limit Enforcement**: Prevent runaway costs with monthly token limits
3. **Rate Limit Customization**: Different rate limits per profile
4. **Easy User Assignment**: Admins can quickly change user tiers
5. **Usage Tracking**: Monthly tracking with automatic reset
6. **Backward Compatible**: Users without profiles fall back to system defaults

## Files Created/Modified

- `src/trusted_data_agent/auth/token_quota.py` - Monthly token usage (`user_token_usage`), quota status
- `src/trusted_data_agent/auth/consumption_enforcer.py` - Pre-execution enforcement, global override
- `src/trusted_data_agent/auth/consumption_manager.py` - `user_consumption` ledger and per-turn checks
- `src/trusted_data_agent/auth/models.py` - `ConsumptionProfile`, `UserTokenUsage`, `User.consumption_profile_id`
- `src/trusted_data_agent/auth/database.py` - Settings and profile bootstrap
- `src/trusted_data_agent/auth/rate_limiter.py` - Token-bucket limiter (IP limits)
- `src/trusted_data_agent/api/auth_routes.py` - Admin and user API endpoints
- `maintenance/assign_unlimited_profiles.py` - Bulk-assign Unlimited
- Gate: `test/test_consumption_profiles.py`

## Support

For questions or issues with consumption profiles:
1. Check logs in `logs/` directory
2. Verify database schema with `sqlite3 tda_auth.db`
3. Test API endpoints with curl
4. Review audit logs for profile assignment changes
