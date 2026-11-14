# Banner Messaging System

The platform has **three distinct banner systems** for displaying messages to users, plus two
companion primitives in the same module (a confirm strip and a prompt modal, §4) and the split
panel's own notice line (§5).

*Last verified against the code: 2026-10-30.*

## 1. Application-Level Banner

**Location:** Top navigation bar (next to "Uderia Platform" title and GitHub star)

**Element ID:** `header-status-message`

**Usage:**
```javascript
// Import the function
import { showAppBanner } from './bannerSystem.js';

// Or use global function (after bannerSystem.js is loaded)
showAppBanner('Operation successful!', 'success');
showAppBanner('Something went wrong', 'error', 3000); // 3 second duration
```

**When to use:**
- ✅ Global configuration changes
- ✅ Authentication/authorization messages
- ✅ RAG operations (create/delete collections)
- ✅ Admin operations (user management, system settings)
- ✅ Credential management
- ✅ Access token management
- ✅ MCP server operations
- ✅ Profile tier changes
- ✅ System-wide notifications

**Types:** `success` (green), `error` (red), `warning` (yellow), `info` (blue)

**Above a modal:** when a modal overlay is open (`.tpl-modal-overlay`, a visible `*-modal-overlay`,
or a visible `*-modal.fixed`), the same call renders as a floating toast (`.app-banner-toast`,
appended to `body`) instead of the navbar span, so a message raised from inside a dialog is not
hidden behind it. One toast element is reused; a new message replaces the previous one.

**Server-pushed app banners:** some notices on the per-user notification channel
(`/api/notifications/subscribe`, rendered in `static/js/notifications.js`) end in `showAppBanner`:
- `db_health` — the platform database is unreadable (`core/db_health.report_unreadable`, raised
  by the SQLite pool's unreadable-file hook; broadcast to every open stream without a database
  read, re-sent every 10 minutes while the state lasts); shown as `error` for 60 s.
- trust-event notices (`trust_events.broadcast_notice`), review decisions and the trust breaker —
  the tone follows the notice's `severity`.

---

## 2. Conversation-Level Banner

**Location:** Inside the conversation pane (below profile selector, above chat area)

**Element ID:** `profile-override-warning-banner`

**Usage:**
```javascript
// Import the function
import { showConversationBanner, hideConversationBanner } from './bannerSystem.js';

// Or use global function (after bannerSystem.js is loaded)
showConversationBanner('Profile override active', 'warning', true); // dismissible
hideConversationBanner();
```

**When to use:**
- ✅ Profile override warnings/errors
- ✅ Conversation-specific errors
- ✅ Query submission issues
- ✅ Context/history warnings
- ✅ Session-specific messages
- ✅ Token/context limit warnings
- ✅ Streaming connection issues

**Types:** `success` (green), `error` (red), `warning` (yellow), `info` (blue)

---

## 3. Maintenance Banner

**Location:** Full-width bar between the top nav and the main content area

**Element ID:** `maintenance-banner` (+ child spans `maintenance-banner-text`, `maintenance-banner-countdown`, `maintenance-banner-downtime`)

**Usage:**
```javascript
// Show banner (triggered via SSE from admin action)
window.showMaintenanceBanner(message, countdownMinutes, downtimeMinutes);

// Hide banner (user dismiss or admin cancel)
window.hideMaintenanceBanner();
```

**When to use:**
- ✅ Platform restart notifications (before manual docker/process restart)
- ✅ Scheduled maintenance windows
- ✅ Any situation requiring a **persistent**, platform-wide alert with a live countdown

**Behaviour:**
- Persistent — does not auto-hide; stays until the user dismisses it (×) or admin sends a cancel
- Live countdown ticking in **MM:SS** (minutes and seconds); switches to "System restart imminent — save your work now!" at 00:00
- Optional estimated downtime pill (hidden when set to 0)
- Late-joiner support: active banner stored in `APP_STATE["maintenance_banner"]` and sent immediately to new SSE subscribers
- Theme-aware: uses `var(--teradata-orange)` and `var(--text-primary)` CSS variables

**Admin trigger:** Administration → App Config → Operations → System Restart → "Notify Users" button
(`POST /api/v1/admin/system-restart/notify` / `POST /api/v1/admin/system-restart/cancel`)

---

## 4. Confirm Strip and Prompt Modal

Two more primitives live in `bannerSystem.js`:

- **`showConfirmBanner(message, onConfirm, { confirmText, cancelText })`** — the destructive
  yes/no strip in the top navigation (`#delete-confirmation-banner`, class `.app-banner-confirm`).
  Use it for deletes, unpublish, disconnect — never the browser's `confirm()` (it is only the
  last-resort fallback when the strip's elements are missing).
- **`showPromptModal({ title, body, label, placeholder, hint, confirmText, cancelText, required,
  multiline, context })`** — a themed modal that COLLECTS text and resolves to the trimmed value
  or `null`. `required` keeps the confirm button disabled until text is entered; Enter submits a
  single-line prompt, Cmd/Ctrl+Enter a multiline one; Escape or a backdrop click cancels. Use it
  instead of `window.prompt()`.

---

## 5. Split Panel Notice Line

A message about what a split panel shows (a canvas changed in another tab, a flow saved
elsewhere) is not a banner: it is the panel's own notice line,
`showSplitPanelNotice(panel, text, { actionLabel, onAction, actions })` in
`static/js/handlers/splitPanelChrome.js` (also `window.showSplitPanelNotice`), inserted under the
panel header (`.sp-notice`) with its acts in reading order and a dismiss button;
`hideSplitPanelNotice(panel)` removes it. Every panel uses this one line — never a private one.

---

## Color Standards

All banners use the same four tones, as theme classes defined per theme in
`static/css/themes.css` (`app-banner-success`, `app-banner-error`, `app-banner-warning`,
`app-banner-info`), so they adapt to dark, light and legacy themes:
- **Success**: green
- **Error**: red
- **Warning**: yellow
- **Info**: blue

---

## Quick Decision Guide

**Ask yourself:** "Is this message about the entire application or just this conversation?"

- **Entire application, transient** → Use `showAppBanner()`
  - Examples: "Credentials saved", "User created", "RAG collection deleted"

- **Entire application, persistent with countdown** → Use `showMaintenanceBanner()`
  - Examples: "Platform restarting in 5 minutes", scheduled maintenance window

- **This conversation only** → Use `showConversationBanner()`
  - Examples: "Profile override failed", "Query too long", "Connection lost"

- **A yes/no before a destructive act** → Use `showConfirmBanner()`

- **Text the user must type** → Use `showPromptModal()`

- **About what one split panel shows** → Use `showSplitPanelNotice()`

---

## API Reference

### showAppBanner(message, type, duration)
- `message` (string): Message to display
- `type` (string): 'success', 'error', 'warning', or 'info' (default: 'info')
- `duration` (number): Display duration in milliseconds (default: 5000)

### showConversationBanner(message, type, dismissible)
- `message` (string): Message to display
- `type` (string): 'success', 'error', 'warning', or 'info' (default: 'warning')
- `dismissible` (boolean): Show close button (default: true)

### hideConversationBanner()
- Hides the conversation-level banner

### hideAppBanner()
- Immediately hides the application-level banner

### showMaintenanceBanner(message, countdownMinutes, downtimeMinutes)
- `message` (string): Notification text shown in the banner
- `countdownMinutes` (number): Minutes until restart; starts a live MM:SS countdown (0 = no countdown)
- `downtimeMinutes` (number): Estimated downtime shown as a pill (0 = hidden)

### hideMaintenanceBanner()
- Stops the countdown interval and hides the maintenance banner

### showConfirmBanner(message, onConfirm, options)
- `onConfirm` (function, may be async): run after OK
- `options`: `{ confirmText, cancelText }` (labels reset to OK / Cancel after use)

### showPromptModal(opts)
- Returns `Promise<string|null>` — see §4

---

## Visual Distinction

**Application Banner:**
- Position: Inside nav bar (`#header-status-message` span)
- Color: Blue/Green/Orange/Red background
- Auto-hides after 5 seconds (or the given duration)
- No dismiss button
- Floats as a toast above an open modal

**Conversation Banner:**
- Position: Inside conversation pane
- Color: Yellow/Green/Red tinted background
- Can be persistent or dismissible
- Has icon and optional close button

**Maintenance Banner:**
- Position: Full-width bar below top nav, above main content
- Color: Orange-tinted stripe (`var(--teradata-orange)`)
- Persistent — user-dismissible (×) or admin-cancellable
- Live MM:SS countdown + estimated downtime pill

---

## Implementation Files

- **Core System:** `/static/js/bannerSystem.js` (all six banner functions + `showConfirmBanner` + `showPromptModal`, also exposed on `window`)
- **Server-pushed banners:** `/static/js/notifications.js` (`db_health`, trust notices); backend `src/trusted_data_agent/core/db_health.py`, `core/trust_events.py` → `broadcast_notice`
- **Split panel notice:** `/static/js/handlers/splitPanelChrome.js`
- **HTML Markers:** `/templates/index.html` (search for "BANNER SYSTEM")
- **Usage Examples:** 
  - Application: `/static/js/handlers/accessTokenManager.js`
  - Conversation: Check profile override handlers in conversation code
