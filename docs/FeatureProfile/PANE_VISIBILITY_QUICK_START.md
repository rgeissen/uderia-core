# Pane Visibility — Quick Reference

Administrators decide which user tiers see each view in the sidebar.

**Where:** Administration → **Panes**

## The views and their defaults

The table lists the sidebar's views in sidebar order, under the sidebar's own group headings.

| View | Sidebar group | User | Developer | Admin |
|------|---------------|------|-----------|-------|
| **Reasoning** — conversations with your agents | — | ✅ | ✅ | ✅ |
| **Anatomy** — agent profiles, LLMs, MCP servers, stores, memory, skills and packs | The Agent | ✅ | ✅ | ✅ |
| **Trust** — the trust circle, posture and assurance | The Agent | ✅ | ✅ | ✅ |
| **Awareness** — sessions and performance | The Agent | ❌ | ✅ | ✅ |
| **Ontology** — only when the enterprise Ontology suite is present | The Agent | ✅ | ✅ | ✅ |
| **Intelligence** — knowledge, planners, graphs and skills | Faculties | ❌ | ✅ | ✅ |
| **Components** — the agent's instruments and connectors | Faculties | ❌ | ✅ | ✅ |
| **Marketplace** — share and subscribe | Exchange | ✅ | ✅ | ✅ |
| **Administration** — users, governance and operations | Platform | ❌ | ❌ | ✅ (always) |

## Using it

1. Log in as an admin and open **Administration → Panes**.
2. Toggle a view on or off per tier. The change is saved at once and applies the next time a user of that tier loads the page.
3. **Reset to Defaults** restores the table above.

When every view under a sidebar group is hidden for a tier, the group label is hidden as well.

## How it stays in step with the sidebar

The names, groups, descriptions and order are not stored by hand. They come from the pane catalogue, `src/trusted_data_agent/core/pane_catalog.py`, the one list of the sidebar's views. Every read writes them onto the stored rows, so a renamed view shows its current name without a migration. A new view gets a row with its default visibility the first time the table is read. The admin's visibility choices are never overwritten.

When the sidebar gains, loses or renames a view, change the catalogue in the same edit. `test/test_pane_catalog.py` fails if the catalogue and the sidebar in `templates/index.html` disagree.

## Notes

- Pane visibility hides navigation. It is not an authorization boundary: every endpoint enforces its own tier and ownership checks.
- The **Administration** view is always visible to admins; its Admin toggle is locked.
- No restart is needed after changing visibility.

## API

```http
GET    /api/v1/auth/me/panes                       # panes + flags, for the caller's tier
GET    /api/v1/admin/panes                         # admin: list
PATCH  /api/v1/admin/panes/<pane_id>/visibility    # admin: update flags
POST   /api/v1/admin/panes/reset                   # admin: reset to defaults
```

Full detail: [REST API §3.1.12 and §3.1.12a](../RestAPI/restAPI.md).

## Files

- `src/trusted_data_agent/core/pane_catalog.py` — the catalogue, `sync_panes`, `reset_panes`
- `src/trusted_data_agent/auth/models.py` — `PaneVisibility` (table `pane_visibility`)
- `src/trusted_data_agent/api/admin_routes.py` — admin endpoints
- `src/trusted_data_agent/api/auth_routes.py` — `GET /auth/me/panes`
- `templates/index.html` — `updatePaneVisibility` (applies the flags to the sidebar)
- `static/js/adminManager.js` — the Panes tab
- `test/test_pane_catalog.py` — the gate

## Troubleshooting

**A view does not hide.** Hard-refresh the browser (Ctrl+Shift+R) and check the user's tier.

**The Administration view is missing for an admin.** Check that the user has `profile_tier='admin'`.
