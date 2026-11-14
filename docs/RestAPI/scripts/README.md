# REST API Scripts

This directory contains helper scripts for interacting with the Uderia Platform REST API.

## Prerequisites

- **jq**: JSON processor
  - macOS: `brew install jq`
  - Debian/Ubuntu: `sudo apt-get install jq`
- **curl**: HTTP client (usually pre-installed)

## Authentication

All scripts now use **Bearer token authentication** instead of the deprecated `X-TDA-User-UUID` header.

### Quick Start

1. **Create an Access Token** (recommended for automation):
   ```bash
   ./create_access_token.sh myusername mypassword "My API Token" 90
   ```
   
   This will:
   - Login with your credentials
   - Create a long-lived access token
   - Display the token (save it securely!)

2. **Export the Token** for easy use:
   ```bash
   export TDA_ACCESS_TOKEN="tda_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"
   ```

## Scripts Overview

### 1. `create_access_token.sh`

Creates a new access token for API authentication.

**Usage:**
```bash
./create_access_token.sh <username> <password> [token_name] [expires_in_days]
```

**Examples:**
```bash
# Create token with defaults (90 days)
./create_access_token.sh admin mypassword

# Create token with custom name and expiration
./create_access_token.sh admin mypassword "Production Server" 365

# Create token that never expires
./create_access_token.sh admin mypassword "CI/CD Pipeline" 0
```

**Output:** Displays the full access token (save it immediately!)

---

### 2. `rest_config.sh`

Configures the TDA application with LLM provider and MCP server settings.

**Usage:**
```bash
export TDA_TOKEN=<jwt-or-access-token>   # /api/v1/configure requires authentication
./rest_config.sh <config_file.json>
```

**Examples:**
```bash
# Configure with Google
./rest_config.sh sample_configs/config_google.json

# Configure with Anthropic
./rest_config.sh sample_configs/config_anthropic.json

# Configure with custom file
./rest_config.sh my_custom_config.json
```

**Note:** The script posts to `POST /api/v1/configure` without a token. That endpoint reads the
caller from the token when one is sent and otherwise configures with no user, so on a multi-user
install it does not configure *your* account. The supported path is a **profile** (LLM + MCP server)
created in Anatomy → Agent Profiles, or via `POST /api/v1/profiles` (see
[restAPI.md §3.10](../restAPI.md#310-profile-management)), marked as default and activated.

---

### 3. `rest_run_query.sh`

Complete workflow: creates session, submits query, monitors progress, displays result.

**Usage:**
```bash
./rest_run_query.sh <access_token> "<your_question>" [--session-id <id>] [--verbose]
```

**Examples:**
```bash
# Simple query (creates new session)
./rest_run_query.sh "$TDA_ACCESS_TOKEN" "What databases are available?"

# Use existing session
./rest_run_query.sh "$TDA_ACCESS_TOKEN" "Show me the schema" --session-id abc-123

# Verbose mode (shows all events)
./rest_run_query.sh "$TDA_ACCESS_TOKEN" "Analyze the data" --verbose

# Combining flags
./rest_run_query.sh "$TDA_ACCESS_TOKEN" "Generate report" --session-id xyz-789 --verbose
```

**Output:**
- Without `--verbose`: Only shows final result (progress to stderr)
- With `--verbose`: Shows all events and progress

The script always uses your **default profile** (it sends only `prompt`). To run a query on another
profile, call the query endpoint directly with `"profile_id"` in the body and the **bare question** in
`prompt` — never an `@TAG` (the REST path does not strip it; see
[PROFILE_OVERRIDE_FEATURE.md](../PROFILE_OVERRIDE_FEATURE.md)). The profile must be activated, else the
query answers `403 profile_not_activated`.

---

### 4. `rest_check_status.sh`

Polls a task URL until completion, showing progress updates.

**Usage:**
```bash
./rest_check_status.sh <task_url_path> <access_token> [--verbose]
```

**Examples:**
```bash
# Check task status
./rest_check_status.sh "/api/v1/tasks/task-123-456" "$TDA_ACCESS_TOKEN"

# Verbose mode (shows all events as they arrive)
./rest_check_status.sh "/api/v1/tasks/task-123-456" "$TDA_ACCESS_TOKEN" --verbose
```

Polling stops on the terminal statuses `complete`, `error` and `cancelled` (the value is `complete`,
not `completed`; a turn that answered nothing ends `error`).

**Note:** Usually called automatically by `rest_run_query.sh`, but can be used standalone.

---

### 5. `rest_stop_task.sh`

Cancels a running task.

**Usage:**
```bash
./rest_stop_task.sh <task_id> <access_token> [--base-url <url>]
```

**Examples:**
```bash
# Cancel a task
./rest_stop_task.sh task-123-456 "$TDA_ACCESS_TOKEN"

# Cancel with custom base URL
./rest_stop_task.sh task-123-456 "$TDA_ACCESS_TOKEN" --base-url http://remote-server:5050
```

**Output:** Shows cancellation status and server response.

---

## Complete Workflow Example

```bash
# Step 1: Create access token (one-time setup)
./create_access_token.sh myuser mypass "Automation" 90
# Save the token output!

# Step 2: Export token for convenience
export TDA_ACCESS_TOKEN="tda_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"

# Step 3: Configure the application (if not already done)
./rest_config.sh sample_configs/config_google.json

# Step 4: Run queries
./rest_run_query.sh "$TDA_ACCESS_TOKEN" "What databases are available?"
./rest_run_query.sh "$TDA_ACCESS_TOKEN" "Show me customer data from sales_db"
./rest_run_query.sh "$TDA_ACCESS_TOKEN" "Generate a monthly report" --verbose

# Step 5: Cancel a task if needed (get task_id from rest_run_query output)
./rest_stop_task.sh task-123-456 "$TDA_ACCESS_TOKEN"
```

## Environment Variables

For convenience, you can set these environment variables:

```bash
# Required for authentication
export TDA_ACCESS_TOKEN="tda_your_token_here"

# Optional (defaults to http://127.0.0.1:5050)
export TDA_BASE_URL="https://tda.company.com"
```

## Sample Configurations

The `sample_configs/` directory contains example configuration files for all supported LLM providers:

- `config_google.json` - Google Gemini
- `config_anthropic.json` - Anthropic Claude
- `config_openai.json` - OpenAI GPT
- `config_amazon.json` - Amazon Bedrock
- `config_azure.json` - Azure OpenAI
- `config_friendli.json` - Friendli AI
- `config_ollama.json` - Ollama (local)

**Before using:** Edit the config file and replace placeholder values with your actual credentials.

## Security Best Practices

### ✅ DO

- **Store tokens securely** in environment variables or secret managers
- **Use `.env` files** (and add to `.gitignore`)
- **Rotate tokens regularly** (every 90 days recommended)
- **Create separate tokens** for each application/environment
- **Use HTTPS** in production (set `BASE_URL` to https://)
- **Revoke unused tokens** immediately

### ❌ DON'T

- **Don't commit tokens** to version control
- **Don't share tokens** between applications
- **Don't hardcode tokens** in scripts
- **Don't use HTTP** in production environments
- **Don't log tokens** in application logs

## Troubleshooting

### "Authentication required"

**Cause:** Missing or invalid access token

**Solution:**
```bash
# Create new token
./create_access_token.sh username password

# Verify token is set
echo $TDA_ACCESS_TOKEN

# Test token
curl -X GET http://localhost:5050/api/v1/sessions \
  -H "Authorization: Bearer $TDA_ACCESS_TOKEN"
```

### "Profile ... is not activated" (403)

**Cause:** The default profile (or the `profile_id` override) has not been activated for API use.

**Solution:** Activate it in Anatomy → Agent Profiles (all of its tests must pass), or
`POST /api/v1/profiles/set_active_for_consumption`. Note that `POST /api/v1/profiles/{id}/activate`
switches the profile context but does **not** enable API consumption.

### "Connection refused"

**Cause:** TDA server not running

**Solution:**
```bash
# Check if server is running
curl http://localhost:5050/

# Start the server
cd /path/to/uderia
python -m trusted_data_agent.main
```

### "Application not configured"

**Cause:** LLM and MCP not set up

**Solution:**
```bash
# Configure via script
./rest_config.sh sample_configs/config_google.json

# Or via web UI
open http://localhost:5050
# Anatomy → Agent Profiles: create a profile (LLM + MCP server), set it as default, activate it
```

### "jq: command not found"

**Cause:** jq not installed

**Solution:**
```bash
# macOS
brew install jq

# Debian/Ubuntu
sudo apt-get install jq

# Windows (via Chocolatey)
choco install jq
```

## Migration from Old Scripts

If you have old scripts using `X-TDA-User-UUID`:

**Old format:**
```bash
curl -H "X-TDA-User-UUID: user-uuid-here" http://localhost:5050/api/v1/sessions
```

**New format:**
```bash
curl -H "Authorization: Bearer tda_your_token_here" http://localhost:5050/api/v1/sessions
```

**Migration steps:**
1. Create access token: `./create_access_token.sh username password`
2. Replace `X-TDA-User-UUID: $USER_UUID` with `Authorization: Bearer $ACCESS_TOKEN`
3. Update all scripts and automation

## Additional Resources

- **Full REST API Documentation:** [../restAPI.md](../restAPI.md)
- **Main Documentation:** [../../../README.md](../../../README.md)
- **Authentication Guide:** See Section 2 of [../restAPI.md](../restAPI.md)
- **Code Examples:** See Section 5 of [../restAPI.md](../restAPI.md)

## Support

For issues or questions:
1. Check the [troubleshooting section](#troubleshooting)
2. Review the [full API documentation](../restAPI.md)
3. Check the server log (stdout, or wherever you redirected it, e.g. `/tmp/uderia_server.log`)
4. Open an issue on GitHub

---

**Last Updated:** October 30, 2026
