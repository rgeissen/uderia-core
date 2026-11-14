# Phase 4 Quick Test Guide

> **Status (2026-10-30):** historical record of the Dec 2024 – early 2025 prompt-schema migration; kept as-is. Since then profiles live per user in `user_preferences.preferences_json` (`tda_auth.db`) rather than `tda_config.json` (now a read-only bootstrap template), and dev tools moved to `schema/dev/`. Current reference: [SCHEMA_DOCUMENTATION.md](./SCHEMA_DOCUMENTATION.md).

## Prerequisites
1. Restart the application to load new code:
   ```bash
   # Stop current server (Ctrl+C)
   # Restart with:
   python -m trusted_data_agent.main
   ```

2. Login as user with Prompt Engineer or Enterprise tier

3. Navigate to Admin Panel → System Prompts tab (Tab 6)

---

## Test 1: Parameters Viewer

**Steps:**
1. Select a prompt from dropdown (e.g., "Master System Prompt")
2. Scroll down to "Prompt Parameters" section
3. Click "Show" button

**Expected Results:**
- ✅ Section expands smoothly
- ✅ Table shows parameters with 4 columns: Parameter, Type, Value, Scope
- ✅ Global parameters have blue "Global" badge
- ✅ Prompt parameters have purple "Prompt" badge
- ✅ Values displayed in monospace font with gray background
- ✅ Button text changes to "Hide"

**To Verify:**
```bash
# Check browser console for API call
# Should see: GET /api/v1/system-prompts/MASTER_SYSTEM_PROMPT/parameters
```

---

## Test 2: Version History Viewer

**Steps:**
1. With same prompt selected, scroll to "Version History" section
2. Click "Show" button

**Expected Results:**
- ✅ Section expands smoothly
- ✅ Table shows version 1 with green "Active" badge
- ✅ Date/time formatted correctly (e.g., "1/28/2025, 10:00:00 AM")
- ✅ Author shows "system"
- ✅ Description shows "Initial version"
- ✅ Button text changes to "Hide"

**To Verify:**
```bash
# Check browser console for API call
# Should see: GET /api/v1/system-prompts/MASTER_SYSTEM_PROMPT/versions
```

---

## Test 3: Diff Viewer (No Override)

**Steps:**
1. Select a prompt you haven't modified yet
2. Scroll to "Compare Base vs Override" section
3. Click "Show" button

**Expected Results:**
- ✅ Section expands smoothly
- ✅ Left pane shows base prompt content
- ✅ Right pane shows "No override found"
- ✅ Base length shows character count
- ✅ Override length shows 0
- ✅ Difference shows 0

---

## Test 4: Diff Viewer (With Override)

**Steps:**
1. Select a prompt from dropdown
2. Click "Load Current Prompt"
3. Modify the text in textarea
4. Click "Save System Prompt"
5. Scroll to "Compare Base vs Override" section
6. If hidden, click "Show"

**Expected Results:**
- ✅ Section shows both base and override content
- ✅ Left pane has gray dot indicator
- ✅ Right pane has orange dot indicator
- ✅ Both panes scrollable if content is long
- ✅ Statistics show correct character counts
- ✅ Difference shows positive/negative value with color coding

**To Verify:**
```bash
# Check browser console for API call
# Should see: GET /api/v1/system-prompts/<prompt_name>/diff
```

---

## Test 5: Toggle Functionality

**Steps:**
1. Expand all 3 sections (Parameters, Versions, Diff)
2. Click "Hide" on each section

**Expected Results:**
- ✅ Each section collapses smoothly
- ✅ Button text changes back to "Show"
- ✅ No console errors
- ✅ Can re-expand sections

---

## Test 6: Prompt Switching

**Steps:**
1. Expand all 3 sections for one prompt
2. Switch to a different prompt in dropdown
3. Click "Show" on each section again

**Expected Results:**
- ✅ Data updates to reflect new prompt
- ✅ Parameters change based on new prompt
- ✅ Version history shows new prompt's history
- ✅ Diff shows new prompt's base/override

---

## Debugging Commands

If tests fail, check these:

**1. Check if endpoints are registered:**
```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:5001/api/v1/system-prompts/MASTER_SYSTEM_PROMPT/parameters
```

**2. Check database has data:**
```bash
sqlite3 tda_auth.db "SELECT COUNT(*) FROM global_parameters;"
sqlite3 tda_auth.db "SELECT COUNT(*) FROM prompt_parameters;"
sqlite3 tda_auth.db "SELECT COUNT(*) FROM prompt_versions;"
```

**3. Check browser console:**
- Open Developer Tools (F12)
- Go to Console tab
- Look for errors or failed API calls

**4. Check server logs:**
```bash
tail -f logs/tda_api.log | grep "system-prompts"
```

---

## Expected Database Counts

Based on migration:
- Global parameters: 9
- Prompt parameters: 17
- Prompt versions: 12 (one per prompt)
- Prompts: 12

---

## Common Issues

**Issue:** Sections don't expand  
**Fix:** Check browser console for JavaScript errors

**Issue:** "Select a prompt to view parameters"  
**Fix:** Ensure prompt is selected in dropdown before clicking Show

**Issue:** API returns 401 Unauthorized  
**Fix:** Re-login to get fresh auth token

**Issue:** Empty tables  
**Fix:** Verify migration ran successfully (check database counts above)

**Issue:** Diff shows "No override found" even after saving  
**Fix:** Hard refresh page (Ctrl+Shift+R) to clear cache

---

## Success Criteria

Phase 4 is working correctly if:
- ✅ All 3 sections expand/collapse without errors
- ✅ Parameters display correctly with color-coded badges
- ✅ Version history shows at least 1 version per prompt
- ✅ Diff viewer works for both override/no-override cases
- ✅ Switching prompts updates all sections
- ✅ No console errors during normal operation

---

**Time to Complete:** ~10 minutes  
**If all tests pass:** Phase 4 is ready for production! 🎉
