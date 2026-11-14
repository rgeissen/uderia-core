# Consumption Profiles and Usage Quotas

Uderia includes a comprehensive **consumption profile enforcement system** that provides granular control over resource usage across different user tiers and deployment scenarios.

## Overview

Consumption profiles enable administrators to:
- Set per-user rate limits on prompts (hourly and daily)
- Enforce monthly token quotas (input and output tokens tracked separately)
- Override profiles with global emergency limits
- Suspend a tier by deactivating its profile
- Track usage in real-time with detailed audit trails

## Predefined Profile Tiers

Four consumption profiles are available out-of-the-box:

| Profile | Prompts/Hour | Prompts/Day | Input Tokens/Month | Output Tokens/Month | Config Changes/Hour* |
|---------|--------------|-------------|-------------------|---------------------|---------------------|
| **Free** | 50 | 500 | 100,000 | 50,000 | 5 |
| **Pro** | 200 | 2,000 | 500,000 | 250,000 | 20 |
| **Enterprise** | 500 | 5,000 | 2,000,000 | 1,000,000 | 50 |
| **Unlimited** | 1,000 | 10,000 | ∞ | ∞ | 100 |

\* Creating, changing or deleting an agent, an LLM configuration or an MCP server counts as a configuration change. Over the limit the request is refused with HTTP 429 (`type: rate_limit_exceeded`); each successful change is recorded in the audit log as `config_change`, which is what the limit counts. Activating a configuration is not counted, and admins are exempt.

By default, new users receive the **Unlimited** profile. Administrators can change the default profile or assign specific profiles to individual users.

## Profile Management

**Administrators can:**
- Create and configure custom consumption profiles
- Assign profiles to specific users
- Deactivate a profile without deleting it. A deactivated profile blocks every user assigned to it from running prompts, so it suspends a tier; it is not a dry run.
- View real-time consumption statistics per user
- Set global override mode for emergency rate limiting

**Testing a consumption profile:** assign it to a non-admin test user with low limits (e.g. 1 prompt/hour) to verify enforcement. (*Active for Consumption* is a different switch: it sits on an **agent** profile, requires the agent's tests to pass, and decides whether the agent is offered for `@TAG` and REST use.)

## Rate Limiting Configuration

Access rate limiting controls through **Administration → Config → Security → Rate Limiting**:

**Enable Rate Limiting:**
- Master switch for the per-IP login and registration limits
- **On by default**; switch it off for a single-user installation
- Consumption-profile quotas, and Global Override's per-user limits, are enforced independently of this switch

**Global Override Mode:**
- Emergency toggle to override ALL user profiles
- Forces global limits on all users (including Enterprise/Unlimited)
- Per-user fallback limits applied when profiles aren't assigned
- Useful for system-wide capacity management

**Per-User Limits (when Global Override is enabled):**
- Prompts per Hour (default: 100)
- Prompts per Day (default: 1,000)
- Configuration Changes per Hour (default: 10)

Global Override sets no token limits.

**Per-IP Limits (enforced while Enable Rate Limiting is on):**
- Login attempts per minute
- Registrations per hour
- API calls per minute, for `/api/` requests that carry no valid credential (signed-in use is governed per user; the notification stream is not counted). A refusal is HTTP 429 with `Retry-After`.

## Enforcement Behavior

Two checks run on a prompt:

1. **The UI routes** (the conversation stream and simple chat) check prompts per hour and per day, and the monthly token quota. A refusal is HTTP 429 with `type: rate_limit_exceeded`.
2. **Every turn**, from the UI and from the REST API, is checked against the user's consumption record: prompts per hour and per day, and the monthly token quota.

Both checks read the same limits, resolved when the check runs (`auth/consumption_limits.py`): admins are exempt; Global Override's limits apply when it is on; otherwise the user's assigned profile, or the default profile. Reassigning a user, editing a profile or turning on Global Override takes effect on the user's next prompt, and a deactivated profile blocks both checks.

**Error handling:**
- Clear error messages when limits are exceeded; login and registration refusals also carry `retry_after`
- Fail-open design: allows execution if enforcement check fails
- Full audit trail in logs and database

## Usage Tracking and Reporting

The system maintains detailed consumption records:
- **Per-turn tracking** - Individual prompt costs and token usage
- **Session aggregation** - Cumulative costs per conversation
- **User summaries** - Total consumption across all sessions
- **Historical trends** - Month-over-month usage analytics
- **Audit trail** - Complete record of all consumption events

View consumption details through:
- **Awareness** - sessions, tokens, cost and velocity; **Administration → Cost** for pricing
- **REST API** - Programmatic access to consumption data
- **Database exports** - Complete audit trail for compliance

## Best Practices

**For single-user installations:**
- Switch rate limiting off if you do not need it (it is on by default)
- Use Unlimited profile for maximum flexibility

**For team deployments:**
- Keep rate limiting on
- Assign profiles based on user roles
- Use Global Override for emergency capacity management
- Monitor consumption trends through Awareness and Administration → Cost

**For testing:**
- Assign a dedicated profile to a test user in Administration → Quotas
- Set low limits (1 prompt/hour) to verify enforcement
- Check logs for detailed enforcement flow
- Test with non-admin users for accurate results

**For production:**
- Confirm rate limiting is on before go-live
- Set appropriate default profile for new users
- Monitor consumption patterns and adjust profiles
- Use token quotas to manage monthly costs
- Review audit logs periodically for anomalies

[Consumption enforcement & cost architecture →](../Architecture/COST_TRACKING_ARCHITECTURE.md)

---

**Supported LLM Providers:**
- AWS Bedrock (requires: Access Key, Secret Key, Region)
- Anthropic Claude (requires: API Key)
- OpenAI (requires: API Key)
- Google Gemini (requires: API Key)
- Azure OpenAI (requires: Endpoint, API Key, Deployment Name)
- Friendli.AI (requires: API Key)
- OpenRouter (requires: API Key)
- xAI Grok (requires: API Key)
- Celeris (requires: API Key, optional base URL)
- MLX (local Apple Silicon - requires: host URL)
- Ollama (local - requires: Ollama installation)
