# Profile-Based Classification System - User Guide

> **Status (2026-11-07):** verified against `api/rest_routes.py` (create and update accept `classification_mode` `light` or `full` only; anything else is a 400) and the agent editor's *Tool & Prompt Classification* section. Light is the default. An earlier `none` mode no longer exists.

## Overview

The Uderia Platform now supports **profile-based classification** for MCP (Model Context Protocol) tools, prompts, and resources. Each profile can have its own classification mode, allowing you to customize how capabilities are organized in the UI.

## Classification Modes

### 💡 Light (Recommended)
**Best for:** Fast startup with basic organization

- **Generic single category** per type
- **Categories:** one per type, shown in the panel as "MCP Tools", "MCP Prompts", "MCP Resources" (stored as `All Tools`, `All Prompts`, `All Resources`)
- **Performance:** Fast loading, no LLM classification needed
- **Use case:** Production environments where speed matters more than detailed categorization

**Example UI:**
```
MCP Tools
├─ base_columnDescription
├─ dba_databaseSpace
├─ qlty_columnSummary
└─ ...all tools

MCP Prompts
├─ base_teradataQuery
├─ tdvs_rag_prompt
└─ ...all prompts
```

### 🎯 Full (Semantic)
**Best for:** Rich categorization and discoverability

- **LLM-powered semantic categorization**
- **Categories:** Dynamic based on capability analysis (e.g., "Data Quality", "Table Management", "Performance")
- **Performance:** Initial classification takes ~5-15 seconds, then cached
- **Use case:** Production environments with many tools where discoverability is important

**Example UI:**
```
Data Quality
├─ qlty_columnSummary
├─ qlty_distinctCategories
├─ qlty_missingValues
└─ qlty_univariateStatistics

Table Management
├─ base_tableList
├─ base_tableDDL
├─ base_tablePreview
└─ dba_tableSpace

Performance Analysis
├─ dba_resusageSummary
├─ dba_sessionInfo
└─ dba_flowControl
```

## How to Set Classification Mode

### When Creating a Profile

1. Click **"Add Profile"** in Anatomy → Agent Profiles
2. Fill in basic profile information (name, tag, description)
3. Select your LLM and MCP server
4. In the **"Tool & Prompt Classification"** section, choose one of:
   - ⚪ **Light** - Generic categories (recommended)
   - ⚪ **Full** - Semantic categorization (LLM-powered)
5. Continue configuring tools/prompts/resources
6. Click **"Save"**

### When Editing an Existing Profile

1. Find the profile in Anatomy → Agent Profiles
2. Click **"Edit"**
3. Scroll to **"Tool & Prompt Classification"**
4. Select a different mode
5. Click **"Save"**

**Note:** Changing the classification mode clears cached results. The next time this profile is activated, classification will run fresh.

## How Classification Works

### Initial Classification (Full Mode Only)

When you first activate a profile with `classification_mode: full`:

1. **MCP Connection**: Application connects to the MCP server
2. **Tool Discovery**: All tools, prompts, and resources are loaded
3. **LLM Analysis**: The profile's LLM analyzes each capability's description
4. **Categorization**: Tools/prompts/resources are grouped into semantic categories
5. **Caching**: Results are saved to the profile for instant reuse

**Time:** ~5-15 seconds (one-time per profile per MCP server)

### Cached Classification

On subsequent activations:

1. **Cache Check**: System checks if profile has cached classification
2. **Mode Validation**: Ensures cached mode matches current mode
3. **Instant Load**: Cached results loaded immediately
4. **No LLM Call**: Classification is skipped

**Time:** Instant (< 100ms)

### Mode-Aware Cache Invalidation

The cache is automatically cleared when:
- ✅ Classification mode changes (light ↔ full)
- ✅ Profile is explicitly reclassified via UI button
- ✅ MCP server configuration changes significantly

The cache is **NOT** cleared when:
- ❌ Profile name/tag/description changes
- ❌ Tool/prompt selections change
- ❌ LLM model changes (unless you reclassify)

## Manual Reclassification

You can force reclassification at any time:

1. Find the profile in Anatomy → Agent Profiles
2. Click the **"Reclassify"** button (purple)
3. Confirm the action
4. If the profile is currently active, reclassification runs immediately
5. Otherwise, reclassification will occur on next activation

**When to reclassify:**
- After updating MCP server with new tools
- After changing tool descriptions
- When you want to refresh semantic categories

## Profile Card Badges

Each profile card shows its classification mode:

- 💙 **Light** - Blue badge, generic categories
- 💜 **Full** - Purple badge, semantic categories

Example:
```
┌─────────────────────────────────────────────┐
│ ⭐ Google - Full Stack                      │
│ @GOGET                                      │
│                                             │
│ LLM: Google / gemini-1.5-flash             │
│ MCP: Teradata VantageCloud Lake            │
│ Classification: [Full]                      │
│                                             │
│ [Test] [Reclassify] [Copy] [Edit] [Delete] │
└─────────────────────────────────────────────┘
```

## Performance Comparison

| Mode  | First Load | Subsequent Loads | LLM Calls | Categories | Discoverability |
|-------|------------|------------------|-----------|------------|-----------------|
| Light | Instant    | Instant          | 0         | 3 generic  | Medium          |
| Full  | 5-15s      | Instant          | 1         | Dynamic    | High            |

## Best Practices

### Development Environment
- **Recommended:** `light`
- Fast iteration, minimal overhead
- Classification not needed for testing

### Production Environment (Few Tools)
- **Recommended:** `light`
- Fast startup, adequate organization
- Generic categories sufficient for small toolsets

### Production Environment (Many Tools)
- **Recommended:** `full`
- Initial classification cost is worth it
- Semantic categories greatly improve discoverability
- Cache ensures subsequent loads are instant

### Multiple Profiles Strategy

Create profiles for different use cases:

```
┌─────────────────────┬──────────┬─────────────────┐
│ Profile             │ Mode     │ Purpose         │
├─────────────────────┼──────────┼─────────────────┤
│ Development Testing │ light    │ Fast iteration  │
│ Quick Production    │ light    │ Speed priority  │
│ Full Production     │ full     │ Rich UX         │
│ Demo & Training     │ full     │ Discoverability │
└─────────────────────┴──────────┴─────────────────┘
```

## API Integration

### Get Classification Results

```bash
GET /api/v1/profiles/{profile_id}/classification
```

**Response:**
```json
{
  "status": "success",
  "profile_id": "profile-abc123",
  "classification_mode": "full",
  "classification_results": {
    "tools": {
      "Data Quality": [...],
      "Table Management": [...]
    },
    "prompts": {
      "Query Generation": [...]
    },
    "resources": {}
  }
}
```

### Force Reclassification

```bash
POST /api/v1/profiles/{profile_id}/reclassify
```

**Response:**
```json
{
  "status": "success",
  "message": "Profile reclassified successfully",
  "profile_id": "profile-abc123"
}
```

### Activate Profile

```bash
POST /api/v1/profiles/{profile_id}/activate
```

**Response:**
```json
{
  "status": "success",
  "message": "Switched to profile profile-abc123",
  "classification_mode": "full",
  "used_cache": true
}
```

## Troubleshooting

### Classification Taking Too Long

**Problem:** Initial classification with `full` mode takes > 30 seconds

**Solutions:**
1. Check LLM response time (may be slow API)
2. Consider using `light` mode instead
3. Reduce number of tools/prompts if possible
4. Check network connectivity to LLM provider

### Categories Not Appearing

**Problem:** Using `full` mode but seeing flat lists

**Possible causes:**
1. Classification hasn't run yet - activate the profile
2. Classification failed - check logs for errors
3. Cached in wrong mode - click "Reclassify"

**Solution:**
```bash
# Check classification status
GET /api/v1/profiles/{profile_id}/classification

# Force reclassification
POST /api/v1/profiles/{profile_id}/reclassify
```

### Cache Not Working

**Problem:** Classification runs every time despite cache

**Check:**
1. Classification mode changed? Cache is invalidated
2. Profile modified? Some changes trigger reclassification
3. MCP server changed? New server needs new classification

**Verify cache:**
```bash
GET /api/v1/profiles/{profile_id}/classification

# Check for non-empty classification_results
```

## Migration from Global Setting

If you're upgrading from a version with global `ENABLE_MCP_CLASSIFICATION`:

### Old Behavior (Deprecated)
```python
# tda_config.json or environment
ENABLE_MCP_CLASSIFICATION = true  # Applied to ALL profiles
```

### New Behavior (Current)
```json
{
  "profiles": [
    {
      "id": "profile-1",
      "name": "Production",
      "classification_mode": "full"  // Per-profile setting
    },
    {
      "id": "profile-2", 
      "name": "Development",
      "classification_mode": "light"  // Different per profile
    }
  ]
}
```

### Migration Steps

1. **Automatic Migration:** Existing profiles default to `classification_mode: full`
2. **Review Profiles:** Check each profile's mode in UI
3. **Optimize:** Change modes based on use case (see Best Practices)
4. **Test:** Verify classification works as expected

**Migration script:**
```bash
python migrate_profile_classification.py
```

## FAQ

**Q: Can I change classification mode without losing tool selections?**  
A: Yes, only classification cache is cleared. Tool/prompt selections are preserved.

**Q: Does classification affect which tools are available?**  
A: No, it only affects how they're organized in the UI. All enabled tools remain available.

**Q: Can different profiles have different classification modes?**  
A: Yes! Each profile independently controls its classification mode.

**Q: What happens if LLM fails during classification?**  
A: System falls back to `light` mode automatically and logs the error.

**Q: Is classification stored in the database?**  
A: Results are cached in the user's database configuration.

**Q: Can I see classification in API responses?**  
A: Yes, use `GET /api/v1/profiles/{id}/classification` endpoint.

## Support

For issues or questions:
- Check application logs for detailed error messages
- Review this guide's Troubleshooting section
- Contact your system administrator

---

**Last Updated:** November 24, 2025  
**Version:** Profile-Based Classification v1.0
