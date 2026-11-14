# Knowledge Graph Subscription — Implementation Plan

**Status:** **Implemented** (May 2026) — gated behind `is_kg_subscriptions_enabled()` (admin opt-in via `kg_marketplace_settings.kg_subscriptions_enabled`; default off).
**Last updated:** 2026-10-30 (link/path fixes; pack-sourced copies — see the addendum at the end). The shared materializer now lives in `core/kg_subscription` (gate `test/test_kg_subscription_core.py`).
> **Point-in-time plan.** The live contract is [KNOWLEDGE_GRAPH_ARCHITECTURE.md](KNOWLEDGE_GRAPH_ARCHITECTURE.md) and [MARKETPLACE_ARCHITECTURE.md](MARKETPLACE_ARCHITECTURE.md).
**Related code:** `src/trusted_data_agent/api/kg_marketplace_routes.py`, `components/builtin/knowledge_graph/graph_store.py`, `static/js/handlers/marketplaceHandler.js`, `static/js/handlers/knowledgeGraphPanelHandler.js`
**REST API reference:** [`docs/RestAPI/restAPI.md` §3.28.10–3.28.13](../RestAPI/restAPI.md#32810-subscribe-to-knowledge-graph)
**Verification:** `scripts/verify_kg_subscriptions.sh`

## Implementation Status

| Phase | Status | Notes |
|---|---|---|
| 1. Schema migration | ✅ Implemented | `schema/37_kg_subscriptions.sql` — additive ALTERs, idempotent re-apply |
| 1. Read-only gate on `graph_store` | ✅ Implemented | `KnowledgeGraphReadOnlyError` raised by 7 write methods; sync/unsubscribe bypass via raw SQL |
| 2. Subscribe / unsubscribe / sync / list endpoints | ✅ Implemented | All four endpoints behind `is_kg_subscriptions_enabled()` flag |
| 2. Browse + detail field extension | ✅ Implemented | `subscriber_count`, `export_content_hash`, `is_subscribed`, `subscription_id`, `is_sync_available` |
| 3. Auto-sync on republish | ✅ Implemented | Republish-in-place via existing publish endpoint; thread-pool fanout via `_schedule_auto_sync` |
| 4. Cascade on unpublish | ✅ Implemented | Subscriber local copies hard-deleted via `_delete_subscription_local_kg` |
| 5. Session-archive integration | ✅ Implemented (no-op by design) | Sessions look up active KG per profile dynamically; removing assignments is sufficient |
| 6. Pack-export gate | ✅ Implemented | Subscribed KGs skipped with warning at export time |
| 7a. Marketplace card UI | ✅ Implemented | Subscribe / Sync / Unsubscribe + status indicators |
| 7b. KG panel UI | ✅ Implemented | Lock icon, "Subscribed from <publisher>", Sync/Unsubscribe in both resource panel and Intelligence-tab cards |
| 8. Verification script | ✅ Implemented | `scripts/verify_kg_subscriptions.sh` (publisher-side flow) |
| 8. Docs | ✅ Implemented | REST API doc §3.28.10–13, CLAUDE.md schema list + Recent Major Changes |

**Deferred items** (not blocking, can be added later):
- **Tier-based entity-count quotas** at subscribe/sync time — `consumption_enforcer.py` doesn't expose per-resource entity quotas yet; the locked decision (Q5: "apply quotas") needs the underlying enforcer extension first. Subscribe currently materializes without checking tier limits.
- **Toast notification on auto-sync** — locked decision (Q3) was "silent + indicator", which is what shipped. If we change our mind, the SSE-toast variant from §11.2 of this plan is straightforward to add.

---

## 1. Goal & Non-Goals

**Goal:** Add a `Subscribe` action to the KG marketplace alongside the existing `Fork`. A subscriber receives a live-tracking copy of the publisher's KG that automatically reflects publisher updates on republish, mirroring how agent-pack collection subscriptions stay in sync.

**Non-goals (deferred):**
- Real-time streaming updates (publisher edits visible without a republish step) — every existing subscription system propagates only on publisher-initiated republish/sync, and we follow that contract.
- Per-entity diff resolution — if subscriber edits collide with publisher updates, we replace; we do not attempt three-way merge.
- Cross-tenant subscriptions for KGs that are *not* published to the marketplace.

---

## 2. Design Decision: Snapshot-with-Sync

The two existing subscription systems take opposite approaches:

| | Collections | Agent Packs |
|---|---|---|
| Storage | Reference (`source_collection_id`) | Deep copy with ID remap |
| Updates | Automatic (subscribers query publisher store live) | Snapshot — no auto-update; collection sub-refs DO update |
| Edit | Read-only | Editable |

**KGs need a third model:** *snapshot-with-sync*. Reference-only doesn't work because KG data lives in the **subscriber's** `tda_auth.db` (per-user SQLite), not a shared store like Teradata. Agent-pack-style frozen snapshot doesn't satisfy the user's "auto-update on republish" expectation.

**The model:**
- Subscribe = deep copy `kg_entities` / `kg_relationships` from publisher's marketplace export into the subscriber's local KG tables, marked with a back-reference to `marketplace_id` and `source_kg_id`.
- On publisher republish, the subscriber's copy is **rebuilt** from the new export. This is similar to the agent-pack `update_in_place` path: source-of-truth is the publisher; subscriber's local copy is regenerated.
- Subscriber edits to subscribed KG entities are **disallowed** by default (read-only) — guarantees clean rebuilds. This is the key divergence from `Fork`, which yields full ownership.

This delivers the user-visible promise of "live updates" while staying inside the existing per-user SQLite storage model. Sync is publisher-initiated (republish) + on-demand subscriber-initiated (manual "Sync now" button).

---

## 3. Data Model

### 3.1 New table: `kg_subscriptions`

Mirrors `CollectionSubscription` but adds the target-profile dimension because a KG only has meaning when bound to a profile (KG context enrichment runs per-profile).

```sql
CREATE TABLE kg_subscriptions (
    id                     TEXT    PRIMARY KEY,        -- UUID
    user_uuid              TEXT    NOT NULL,           -- subscriber
    source_kg_id           TEXT    NOT NULL,           -- publisher's kg_metadata.kg_id (immutable)
    source_marketplace_id  TEXT    NOT NULL,           -- marketplace_knowledge_graphs.id
    target_profile_id      TEXT    NOT NULL,           -- subscriber's profile that hosts the copy
    local_kg_id            TEXT    NOT NULL,           -- subscriber's kg_metadata.kg_id (newly minted)
    enabled                INTEGER NOT NULL DEFAULT 1,
    subscribed_at          TEXT    NOT NULL,           -- ISO timestamp
    last_synced_at         TEXT    NOT NULL,           -- bumped on every (re)sync
    last_synced_export_hash TEXT,                      -- sha256 of publisher's kg_export.json at last sync
    UNIQUE (user_uuid, source_kg_id, target_profile_id),
    FOREIGN KEY (user_uuid)       REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (target_profile_id) REFERENCES profiles(id) ON DELETE CASCADE
);
CREATE INDEX kg_subscriptions_user_idx       ON kg_subscriptions(user_uuid);
CREATE INDEX kg_subscriptions_source_idx     ON kg_subscriptions(source_marketplace_id);
CREATE INDEX kg_subscriptions_target_kg_idx  ON kg_subscriptions(local_kg_id);
```

Schema migration filename: `schema/37_kg_subscriptions.sql` (planned as `35_…`; shipped as `37`).

### 3.2 Stamps on existing rows

`kg_metadata` adds two **nullable** columns:

```sql
ALTER TABLE kg_metadata ADD COLUMN is_subscribed       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE kg_metadata ADD COLUMN source_subscription_id TEXT;   -- FK to kg_subscriptions.id
```

When `is_subscribed = 1`, the KG is read-only on the subscriber side (UI hides Edit/Delete; backend rejects writes). Same pattern as agent-pack `is_subscribed` profile flag.

### 3.3 Marketplace listing extensions

`marketplace_knowledge_graphs` adds two columns to track sub count + integrity:

```sql
ALTER TABLE marketplace_knowledge_graphs ADD COLUMN subscriber_count    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE marketplace_knowledge_graphs ADD COLUMN export_content_hash TEXT;     -- sha256 of kg_export.json
```

`export_content_hash` is recomputed on every publish/republish; it lets subscribers cheaply check `last_synced_export_hash` to know whether they're up to date.

---

## 4. REST API Surface

All new endpoints live in `src/trusted_data_agent/api/kg_marketplace_routes.py`.

| Method | Path | Purpose |
|---|---|---|
| `POST`   | `/v1/marketplace/knowledge-graphs/<marketplace_id>/subscribe`   | Create subscription, materialize copy into subscriber's target profile |
| `DELETE` | `/v1/marketplace/knowledge-graphs/<marketplace_id>/subscribe`   | Unsubscribe (also accepts `?subscription_id=` for disambiguation when one user has multiple subs of the same KG into different profiles) |
| `POST`   | `/v1/marketplace/knowledge-graphs/<marketplace_id>/sync`        | Subscriber-initiated rebuild from latest publisher export |
| `GET`    | `/v1/knowledge-graph/subscriptions`                              | List the current user's KG subscriptions (with sync status: up_to_date / sync_available / orphaned) |

**Modified endpoints:**

- `GET /v1/marketplace/knowledge-graphs` — each card gains `is_subscribed`, `subscription_id`, `subscriber_count`, `export_content_hash`.
- `GET /v1/marketplace/knowledge-graphs/<id>` — same fields.
- `POST /v1/marketplace/knowledge-graphs/<id>/install` (Fork) — unchanged. Subscribe and Fork are mutually exclusive, but the user can have one of each from the same source under different profiles.
- `DELETE /v1/marketplace/knowledge-graphs/<id>` (publisher unpublish) — extended to cascade-clean subscriber copies (see §6.2).
- `POST /v1/knowledge-graph/<profile_id>/publish` — recomputes `export_content_hash`; if any existing subscriptions exist (republish case) trigger the auto-sync workflow (see §6.3).

---

## 5. Backend Logic

### 5.1 Subscribe (`POST /subscribe`)

Inputs: body `{target_profile_id: "..."}`. Steps (each a transaction boundary; rollback on failure):

1. **AuthZ checks.**
   - User authenticated.
   - `is_kg_marketplace_enabled()` true.
   - Target profile exists and belongs to user (mirror Fork at line 561).
   - Marketplace listing exists, and either `visibility = 'public'` OR the user has a `marketplace_sharing_grants` row OR is the publisher.
   - **Self-subscribe block**: reject with 400 if `publisher_user_id == user_uuid` (publisher would just use their own KG; mirror collection-sub line 10174-10179).
   - **Idempotency**: if a `(user_uuid, source_kg_id, target_profile_id)` row already exists, return 409 with the existing subscription.
   - **Conflict with Fork**: if the target profile already has a forked copy of the same `source_kg_id` (detected via `kg_metadata` columns to be added — see §10), require explicit `?force=true` query param. Without it, return 409 explaining "this profile already has a forked copy". With force=true, the fork remains untouched (it gets a new `kg_id`); both coexist.
2. **Materialize.**
   - Read `marketplace_data/knowledge_graphs/<marketplace_id>/kg_export.json`.
   - Verify `sha256` of file body matches `export_content_hash` on the marketplace row (else 409 "publisher data drift detected").
   - Mint a new `local_kg_id` (UUID).
   - Insert `kg_metadata` row with `is_subscribed=1`, `source_subscription_id=<new>`, `name = "<publisher name> (subscribed)"`, `description` from manifest.
   - Bulk insert `kg_entities` and `kg_relationships` from export (re-keyed to `local_kg_id`).
   - Insert into `kg_profile_assignments` (so the subscribed KG is selectable on the target profile). `is_active=0` by default — subscriber must explicitly activate. Reuses the same activation UX as Fork.
3. **Persist subscription.**
   - Insert `kg_subscriptions` row with `last_synced_at = now`, `last_synced_export_hash = export_content_hash`.
4. **Bookkeeping.**
   - Atomic increment of `marketplace_knowledge_graphs.subscriber_count` (`UPDATE ... SET subscriber_count = subscriber_count + 1`).
5. **Response.**
   - `{status: "ok", subscription_id, local_kg_id, entities_added, relationships_added}`.

If step 2 or 3 fails: roll back inserted `kg_metadata`/`kg_entities`/`kg_relationships`/`kg_profile_assignments`/`kg_subscriptions` rows.

### 5.2 Unsubscribe (`DELETE /subscribe`)

Steps:

1. **Locate subscription.** If `subscription_id` query param given, use it; else look up by `(user_uuid, marketplace_id)` — if multiple matches exist, 400 with a list (subscriber must specify).
2. **Active-default block.** If the local KG is active on a profile that is the user's default profile AND the subscriber has no other KG configured for that profile, surface a warning (frontend-side; backend allows it). Mirrors the agent-pack default-profile warning but doesn't hard-block.
3. **Delete cascade (transactional).**
   - `DELETE FROM kg_relationships WHERE kg_id = local_kg_id`.
   - `DELETE FROM kg_entities WHERE kg_id = local_kg_id`.
   - `DELETE FROM kg_profile_assignments WHERE kg_id = local_kg_id`.
   - `DELETE FROM kg_metadata WHERE kg_id = local_kg_id` (where `is_subscribed=1` — defensive).
   - `DELETE FROM kg_subscriptions WHERE id = subscription_id`.
4. **Decrement** `marketplace_knowledge_graphs.subscriber_count` (clamp at 0).
5. **Session archiving.** If any active sessions reference the subscribed KG (via `assigned_kg_id` or workflow trace), archive them via the unified relationships system (`archive_sessions_by_kg(kg_id, user_uuid)` — to be added; mirrors `archive_sessions_by_collection`).

### 5.3 Sync (`POST /sync`)

Subscriber-initiated. Steps:

1. Locate `kg_subscriptions` row by `(user_uuid, marketplace_id)`. Reject if missing.
2. Read fresh `kg_export.json`. Compute hash.
3. **Fast-path no-op:** if `hash == last_synced_export_hash`, return `{status: "up_to_date"}` without touching tables.
4. **Rebuild within transaction:**
   - `DELETE FROM kg_relationships WHERE kg_id = local_kg_id`.
   - `DELETE FROM kg_entities WHERE kg_id = local_kg_id`.
   - Re-insert from export.
   - `UPDATE kg_metadata SET name=..., description=..., updated_at=now WHERE kg_id = local_kg_id`.
   - `UPDATE kg_subscriptions SET last_synced_at=now, last_synced_export_hash=<hash>`.
5. **Preserve `kg_profile_assignments`.** Don't touch — subscriber's per-profile activation state survives the sync.
6. Return `{status: "synced", entities_added, relationships_added}`.

### 5.4 Auto-sync trigger on publisher republish

After `kg_export.json` is rewritten in `POST /v1/knowledge-graph/<profile_id>/publish`:

1. Compute new `export_content_hash`; update `marketplace_knowledge_graphs`.
2. Background-task: for each `kg_subscriptions` row with `source_marketplace_id == this`, call the same sync code path as §5.3.
3. Failure of any subscriber's sync **does not** fail the publisher's republish — failures are logged + recorded on the subscription row (`last_sync_error TEXT NULL` column to be added).

This mirrors the agent-pack auto-sync via `update_in_place` (CLAUDE.md May 2026 entry).

---

## 6. Cascade Behavior

### 6.1 Subscriber unsubscribes
See §5.2. Local copy hard-deleted; sessions archived.

### 6.2 Publisher unpublishes (`DELETE /marketplace/knowledge-graphs/<id>`)

Mirrors agent-pack unpublish cleanup (`agent_pack_routes.py:949-1106`):

1. Enumerate every `kg_subscriptions` row with `source_marketplace_id == this`.
2. For each subscriber:
   - Delete cascade per §5.2 steps 3-5.
   - Send notification (existing notification system) "The publisher of '<KG name>' has unpublished it. Your subscription has been removed."
3. Delete the marketplace row + filesystem export folder (existing behavior).

### 6.3 Publisher republishes
Auto-sync via §5.4. No subscriber action required; their copy is rebuilt in place. Activation state preserved.

### 6.4 Subscriber deletes target profile
`ON DELETE CASCADE` on `target_profile_id` removes `kg_subscriptions` row. **But** the local KG copy in `kg_metadata`/`kg_entities`/`kg_relationships` won't auto-cascade because they reference `local_kg_id` not `target_profile_id`. We need a profile-deletion hook in `core/config_manager.py` to clean local KGs when their hosting profile is removed. Mirrors how agent-pack profile deletion handles its installations.

### 6.5 Publisher edits visibility from `public` to `targeted`
Existing subscribers retain access (their grant is on the subscription, not on visibility). Only **new** subscriptions are gated by visibility. Same as collections.

### 6.6 Publisher revokes a targeted grant
The user loses subscribe ability for new KGs, but existing subscriptions persist. Optional: surface a publisher-side action "Revoke and remove from existing subscribers" that triggers per-subscriber unsubscribe. Out of scope for v1.

### 6.7 Subscriber's account deleted
Existing `ON DELETE CASCADE` on `user_uuid` cleans `kg_subscriptions`. The local KG rows go via the existing user-purge code path.

---

## 7. Frontend UX

### 7.1 Marketplace card (Browse view, non-publisher)

Replaces the single `Fork` button with two:

```
┌────────────────────────────────────────┐
│ [Subscribe] [Fork] [Rate]              │
└────────────────────────────────────────┘
```

If already subscribed: `[Sync] [Unsubscribe] [Fork] [Rate]` — Fork stays available so users can also create an editable copy.

`[Sync]` is enabled iff `kg.export_content_hash != subscription.last_synced_export_hash`. Otherwise replaced by a small `✓ Up to date` chip.

### 7.2 Subscribe modal

Mirrors Fork modal but with explanatory copy emphasizing read-only + auto-sync:

> Subscribe to **Fitness Database KG** into a profile. Entities and relationships are copied locally and stay synced — when the publisher republishes, your copy auto-updates. Subscribed KGs are read-only; choose Fork instead if you need to edit.

### 7.3 KG panel (Resource Panel)

Subscribed KGs show:
- Lock icon next to title
- "Subscribed from `<publisher_username>`" subline
- "Last synced 2h ago" (relative time)
- `[Sync now]` button when out of date
- Edit/Delete entity buttons hidden
- `[Unsubscribe]` button replaces `[Delete]`

### 7.4 Conflict warnings

When user clicks Subscribe and the target profile already has a forked copy of the same source: confirm modal "This profile already has a fork of this KG. Subscribing creates a second copy that auto-syncs while the fork remains independent. Continue?"

---

## 8. Edge Cases — Comprehensive List

| # | Scenario | Handling |
|---|---|---|
| 1 | User tries to subscribe to their own published KG | Backend rejects 400 ("publisher cannot subscribe to own KG"). |
| 2 | Same source, multiple target profiles | Allowed. `UNIQUE` is on the triple `(user, source_kg_id, target_profile)`. Each profile gets its own local copy + activation. |
| 3 | Subscriber has both a fork and a subscription of the same KG on the same profile | Allowed with `?force=true`. Live as two separate `kg_metadata` rows. UI surfaces both with distinct labels. |
| 4 | Targeted KG, user is a grantee, subscribes, publisher revokes grant | Subscription persists (sticky). Future `Sync` calls succeed (publisher hasn't republished). On next publisher republish auto-sync still runs. *Decision recorded* — alternative would be to detect missing grant and remove. We take the sticky path to match collections. |
| 5 | Publisher changes visibility from public to targeted, existing subscribers | See edge case 4. Subscription persists. |
| 6 | Publisher republishes with destructive change (entity types removed) | Sync replaces; subscriber's old data is gone. Acceptable for v1. Future: notify subscriber pre-sync if entity-type set shrinks. |
| 7 | Publisher republishes while subscriber's sync is mid-flight | Each sync is transactional per subscription. The auto-sync after publish runs sequentially per subscriber. Worst case: one subscriber's sync uses the older hash and the next auto-sync re-applies — eventually consistent. Lock on `kg_subscriptions.id` for the duration of sync. |
| 8 | `kg_export.json` corrupted on disk | Sync fails with `last_sync_error` set; subscription marked stale. UI shows red "sync failed" badge. Manual sync retries. |
| 9 | Subscriber tries to edit a subscribed KG entity via API | Backend rejects with 403 if `kg_metadata.is_subscribed=1`. Same in `update_entity`/`add_entity`/`delete_entity` endpoints (need defensive checks added). |
| 10 | Subscriber unsubscribes while sync running | Lock contention: sync acquires row-level lock; unsubscribe waits or fails-fast with 409. Same pattern as agent-pack subscribe rollback. |
| 11 | Profile that hosts a subscription is a genie coordinator with assigned children | KG is on the coordinator profile. No interaction with child profile assignments. |
| 12 | KG `name` collision with another KG on subscriber's profile | Auto-suffix `(subscribed)` on insert; further collisions append `(2)` etc. Mirrors agent-pack `expand` strategy for tag conflicts. |
| 13 | Marketplace listing deleted but `kg_subscriptions` row orphaned (race) | `Sync` returns 404 + sets `last_sync_error="Source KG no longer in marketplace"`. UI surfaces "Source removed" banner with `[Unsubscribe]` to clean up. Equivalent to the agent-pack orphan handling. |
| 14 | Subscriber forks a subscribed KG via UI ("Convert to Fork") | New action: copies current subscribed entities into a fresh `is_subscribed=0` KG, leaves subscription intact OR auto-unsubscribes. Choice: leave intact (user can always unsubscribe). |
| 15 | Publisher unpublishes pack containing a published KG (via Agent Pack) | Pack unpublish triggers KG unpublish via `agent_pack_resources` cascade. KG unpublish triggers subscriber cleanup per §6.2. Already covered if we route through the unified relationships system. |
| 16 | Sync failure during auto-sync after republish: 1 of N subscribers fails | Other subscribers continue. Failure recorded on the failing subscription. Publisher's republish completes. Notification sent to failing subscriber to retry. |
| 17 | Subscriber on free tier with quotas enforces a KG entity-count limit | Subscribe rejects pre-materialize with 403 if `entity_count > tier_limit`. Sync similarly checks; if shrinks within limit, proceeds. Hooks into existing `consumption_enforcer.py`. |
| 18 | Cross-profile assignment: subscriber assigns subscribed KG to another of their profiles via existing `kg_profile_assignments` | Allowed. The assignment is local; doesn't affect subscription. On unsubscribe, all assignments to `local_kg_id` are removed. |
| 19 | Agent Pack export includes a subscribed KG | **Block at export time.** Agent packs are portable artifacts; subscribed KGs are bound to a marketplace listing that the importer may not have access to. Manifest export skips subscribed KGs and warns. |
| 20 | Restoring a deleted profile from session archive | Out of scope. Subscriptions don't restore — user must re-subscribe. |

---

## 9. Migration & Rollout

### 9.1 Schema migration
`schema/37_kg_subscriptions.sql` is purely additive: new table + nullable columns on existing tables. No data migration needed. All existing forks continue to function (their `is_subscribed=0` is default).

### 9.2 Backend rollout order
1. Land migration + read-only model code (no endpoints exposed).
2. Land subscribe/unsubscribe/sync endpoints behind a feature flag (`is_kg_subscriptions_enabled()` — global config toggle, mirrors `is_kg_marketplace_enabled`).
3. Land auto-sync trigger on publisher republish.
4. Land cascade cleanup on publisher unpublish.
5. Land session-archive integration via unified relationships.

### 9.3 Frontend rollout
1. Add Subscribe button conditional on feature flag.
2. Add subscribed-KG indicator in KG panel.
3. Add Sync button + status.
4. Add Unsubscribe button + confirmation banner.

### 9.4 Feature flag default
Off until Phase 2 + 3 + 5 are deployed and verified.

---

## 10. Required Schema Touchpoints (Cross-Cutting)

Beyond the new table, these existing structures need awareness of subscriptions:

- **`kg_metadata.is_subscribed`** — read in: `graph_store.update_entity`, `add_entity`, `delete_entity`, `update_relationship`, `add_relationship`, `delete_relationship`, `delete_kg`. All become 403 when set.
- **`graph_store.list_all_graphs()`** — return `is_subscribed`, `subscription_id`, `source_marketplace_id`, `last_synced_at` so the panel can render properly.
- **Unified relationships endpoint** (`/v1/artifacts/<type>/<id>/relationships`) — extend to handle `type=knowledge-graph`. Detector reports sessions, profiles, and subscriptions referencing the KG. Cascades on KG delete.
- **KG export in agent packs** (`agent_pack_manager.py`) — skip subscribed KGs; warn if found.
- **Frontend `createMyKnowledgeGraphCard`** — already uses `kg_name` and renders assigned profiles. Needs new branch for `is_subscribed` cards (lock icon, sync button, no Edit/Delete).

---

## 11. Testing Strategy

### 11.1 Unit
- `kg_subscriptions` CRUD via REST API (planned as `test/test_kg_subscriptions.py`; shipped as `test/test_kg_subscription_core.py` + `scripts/verify_kg_subscriptions.sh`).
- Idempotency: double-subscribe returns existing.
- Visibility checks: targeted, public, self-publish blocks.
- Cascade tests: unsubscribe / unpublish / profile-delete → all rows cleaned.

### 11.2 Integration
- Publisher republish triggers subscriber auto-sync (mock by triggering publish endpoint, verifying subscriber's `last_synced_export_hash` advances).
- Sync failure recorded on row, doesn't block publisher.
- Subscribed KG read-only: write attempts return 403.
- Fork + subscribe coexistence on same profile.

### 11.3 End-to-end (REST API patterns from CLAUDE.md)
Verify the four-user scenario:
1. User A publishes KG public.
2. User B subscribes; B sees latest entities.
3. User A republishes with edits.
4. B's auto-sync runs; B sees the edits.
5. B unsubscribes.
6. A unpublishes — B's data already gone.

### 11.4 Verification harness
Add `scripts/verify_kg_subscriptions.sh` (mirrors `verify_update_in_place.sh`): drives publisher → subscriber flow with assertions on row counts, hashes, and cleanup.

---

## 12. Decisions (Locked)

All five open questions resolved — implementation can proceed against these:

1. **Auto-sync timing — async.** Publisher's republish returns immediately; subscriber syncs run on a background runner. Failures captured per-row in `last_sync_error`; do not block the publisher. Implementation: enqueue via the existing scheduler (`scheduled_tasks` row) or a dedicated task runner — TBD during implementation, but the contract is "republish returns before subscribers are necessarily synced".
2. **Manual sync button — yes.** `Sync now` button on subscribed KG cards. Fast-path no-op when `last_synced_export_hash == current export_content_hash`. Surfaces as primary recovery mechanism when auto-sync fails (red badge → retry).
3. **Republish notification — silent + indicator.** No SSE toast on auto-sync. Subscribed KG card displays a passive "Last synced 2m ago" indicator that updates as syncs complete. Matches collection-subscription UX.
4. **Agent-pack export — block + warn.** Pack export skips subscribed KGs and surfaces a warning ("N subscribed KGs were skipped — convert to Fork to include them"). Forks (owned KGs) export normally. Importer never sees a subscription manifest.
5. **Tier limits — apply.** Subscribe and sync run through `consumption_enforcer` before materializing. Over-quota subscriptions return 403 with a message naming the current usage, the KG size, and the cap. Same path as other resource-creation enforcement.

---

## 13. Estimated Effort

| Phase | Files | LOC est. |
|---|---|---|
| Schema migration | 1 sql + 1 model | ~80 |
| Backend endpoints (subscribe/unsubscribe/sync) | `kg_marketplace_routes.py` | ~400 |
| Auto-sync on republish | `kg_marketplace_routes.py`, new helper | ~120 |
| Read-only enforcement on `graph_store` writes | `graph_store.py` | ~60 |
| Cascade on profile delete + unified relationships | `config_manager.py`, `artifact_detectors.py`, `relationship_analyzer.py` | ~150 |
| Agent-pack export gate | `agent_pack_manager.py` | ~30 |
| Frontend: subscribe modal + card + sync button | `marketplaceHandler.js`, `knowledgeGraphPanelHandler.js` | ~350 |
| Tests | `test/test_kg_subscription_core.py` (planned as `test_kg_subscriptions.py`), verify script | ~400 |
| Docs | this file + CLAUDE.md update | ~80 |
| **Total** | | **~1,670** |

Approximate order of magnitude: 2-3 days focused engineering, plus review/iterate cycles.

---

## Addendum (Sep 2026) — the same materializer serves agent-pack subscriptions

The materialize / rebuild / delete bodies moved verbatim into `core/kg_subscription.py`
(`materialize_copy` · `rebuild_copy` · `delete_copy` · `record_sync_error`); the routes in
`kg_marketplace_routes.py` delegate. A second SOURCE now produces copies through it — an
**agent-pack subscription** (`docs/roadmap/PACK_SUBSCRIBE_COMPLETION.md`), one copy per pack
graph for the subscriber's profile of the same tag.

| | KG-listing copy | Pack copy |
|---|---|---|
| `kg_subscriptions.source_kind` | `'kg'` (default) | `'agent_pack'` |
| `source_marketplace_id` | the KG listing | the **pack** listing |
| `source_kg_id` | the publisher's kg_id | the publisher's kg_id (the export entry carries it) |
| `last_synced_export_hash` | hash of the listing's export file | hash of the exported `knowledge_graphs/kg_N.json` bytes |
| Sync | `POST /v1/marketplace/knowledge-graphs/:id/sync` + the KG fan-out (`source_kind = 'kg'` rows only) | `POST /v1/marketplace/agent-packs/:id/sync` + the pack fan-out (`source_kind = 'agent_pack'` rows only) — the materializer's **update mode**: unchanged hash → kept, changed → `rebuild_copy` under the same local id, no longer shipped → `delete_copy` |
| Unsubscribe | `DELETE …/knowledge-graphs/:id/subscribe` | with the pack (`DELETE …/agent-packs/:id/subscribe`), keyed on the removed profiles |
| The KG-marketplace sync / unsubscribe on this row | act | **refuse — `409 managed_by_agent_pack`**, naming the pack sync (`kg_marketplace_routes.pack_managed_refusal`) |
| The KG cards (resource panel + Intelligence tab) | *Subscribed · Sync · Unsubscribe* | *Subscribed via agent pack …*, **Sync pack** (routes to the ONE pack-sync act, `window.syncAgentPackSubscription`), *Removed with the pack* — never an Unsubscribe of its own |
| `GET /v1/knowledge-graph/list` | `is_sync_available` from the listing hash | `source_kind`, `pack_name`, `publisher_username`, `pack_synced_at`, and `is_sync_available` from the PACK's stamps (`content_updated_at` vs the installation's `synced_at` — the marketplace card's own comparison) |

The two fan-outs never touch each other's rows, and a legacy row without `source_kind` reads
as a KG copy. Gates: `test/test_kg_subscription_core.py`, `test/test_pack_subscription_sync.py`
(section K).
