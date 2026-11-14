# Intelligence Marketplace

> **For publishers republishing an existing pack:** use
> `conflict_strategy=update_in_place` on `POST /v1/agent-packs/import`.
> This preserves the marketplace listing id, the publisher's installation
> id, all per-tag profile ids, and all per-ref collection ids — so existing
> subscribers' `CollectionSubscription` rows and materialized profiles
> stay valid without any subscriber action — and, since Sep 2026, the
> in-place update **fans out** to every pack subscriber (`fan_out_pack_sync`):
> their profile copies, knowledge-graph copies and trust are re-materialized
> in place, ids preserved; a subscriber can also pull at any time with
> `POST /v1/marketplace/agent-packs/<id>/sync` (the *Sync* button — an
> *Update available* chip says when the listing's `content_updated_at` is
> later than their copy's `synced_at`).
>
> **`replace` is unsafe for any pack that has been published or has
> subscribers** — it deletes the marketplace listing, the installation
> row, and the source collection, orphaning every subscriber's
> `CollectionSubscription`. Use `replace` only for fresh installs or
> dev-only iteration on packs you know have no subscribers.
>
> Combine `update_in_place` with `dry_run=true` first to preview the
> change plan (preserved IDs, doc add/update/remove counts) without
> committing. If the new manifest drops a tag the previous pack had, the
> import refuses with `400 dropped_tags` unless you pass
> `force_drop_tags=true` (the dropped profiles are removed and their
> sessions archived). `expand` is the third strategy: it auto-renames
> colliding tags and installs alongside, leaving the existing install (and
> its subscribers) untouched. See [docs/RestAPI/restAPI.md](../RestAPI/restAPI.md)
> §3.14.3 for full request/response schemas.
>
> **Knowledge graphs the pack owns are refreshed in place** (Sep 2026): on
> `update_in_place` the graph active for each shipped profile among the
> pack's `knowledge_graph` resources is snapshotted, cleared and re-imported
> from the manifest under the same `kg_id`, assignments and attestation
> identity (response field `kgs_refreshed`); a graph the pack does not own is
> never touched.
>
> **A signature applied after the import reaches subscribers too.** A
> publisher signs after an import (a signature can only cover content that
> exists), so `POST /v1/agent-packs/<installation_id>/sync-subscribers`
> re-runs the same fan-out on demand. When a repository the pack ships is
> renewed by its scheduled sync policy (`core/trust/renewal.renew_after_sync`,
> Oct 2026), the pack's seal is renewed and the fan-out runs automatically,
> so each subscriber's copy reinstates the renewed attestations by content.
>
> The three import-based build pipelines (`agent_packs/uderia_expert/build.py`,
> `agent_packs/vat/pipeline.py`, `agent_packs/andp/build.py`) default to
> `update_in_place` with an automatic fallback to `replace` on first install.
> `agent_packs/policing-data-products/build.py` assembles its profiles through
> the API and re-registers the pack in place (the installation id survives),
> which keeps the listing and subscriber pointers valid the same way.
>
> The architecture of all marketplace product types is
> [docs/Architecture/MARKETPLACE_ARCHITECTURE.md](../Architecture/MARKETPLACE_ARCHITECTURE.md);
> the rest of this file is the original (2025) implementation log for
> collection marketplace phases 1–3, kept as a record.

---

## Implementation Summary (historical record — collection marketplace phases 1–3)

> Status 2026-10-30: phases 1–4 are shipped. The `tda_config.json` and
> migration-script steps below describe the original file-based rollout:
> collections now live in the `collections` table of `tda_auth.db`
> (`tda_config.json` is a read-only bootstrap template), the schema is created
> at startup by `auth/database.py`, and `maintenance/migrate_marketplace_schema.py`
> no longer exists.

## ✅ Completed

### Database Models Added
- `CollectionSubscription` - Reference-based subscriptions to shared collections
- `CollectionRating` - User ratings and reviews (1-5 stars)

### Collection Schema Extended
All collections now include:
```json
{
  "owner_user_id": null,              // User UUID or null (admin)
  "visibility": "private",             // private | shared | public
  "is_marketplace_listed": false,     // Listed in marketplace
  "subscriber_count": 0,              // Cached subscriber count
  "marketplace_metadata": {}          // Extended metadata
}
```

### Files Modified
- ✅ `src/trusted_data_agent/auth/models.py` - Added marketplace models
- ✅ `tda_config.json` - Extended 3 collections with marketplace fields
- ✅ `maintenance/migrate_marketplace_schema.py` - Migration script created
- ✅ `docs/Marketplace/` - Full documentation added

### Migration Results
```
✓ Table 'collection_subscriptions' exists
✓ Table 'collection_ratings' exists  
✓ All 3 collection(s) have required marketplace metadata
✓ MIGRATION COMPLETE - Marketplace schema is ready!
```

---

## 📋 Quick Reference

### Check Migration Status
```bash
# Verify database tables
sqlite3 tda_auth.db "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'collection_%';"

# Check collection metadata
python -c "from trusted_data_agent.core.config_manager import get_config_manager; print(get_config_manager().get_rag_collections()[0])"
```

### Rollback (if needed)
```bash
sqlite3 tda_auth.db "DROP TABLE collection_subscriptions; DROP TABLE collection_ratings;"
# Restore tda_config.json from backup
```

---

## ✅ Phase 2: Collection Ownership & Access Control - COMPLETE

### Implemented Features:
1. ✅ `rag_retriever.py` - Access control methods added
   - `_get_user_accessible_collections()` - Filter by ownership + subscriptions
   - `is_user_collection_owner()` - Validate ownership
   - `is_subscribed_collection()` - Check subscription status
   - `fork_collection()` - Copy collections with full data
   - `_maintain_vector_store()` - Skip maintenance for subscribed collections

2. ✅ `rest_routes.py` - API endpoints secured
   - GET `/v1/rag/collections` - Filter by user access
   - POST `/v1/rag/collections` - Set owner_user_id automatically
   - PUT `/v1/rag/collections/<id>` - Validate ownership
   - DELETE `/v1/rag/collections/<id>` - Validate ownership

3. ✅ Test suite created (`test/test_marketplace_phase2.py`)
   - All 5 tests passing (100% coverage)
   - User accessible collections filtering ✅
   - Ownership validation ✅
   - Subscription checking ✅
   - Fork collection ✅
   - Maintenance skip for subscriptions ✅

### Verification:
```bash
# Run Phase 2 test suite
python test/test_marketplace_phase2.py
# Expected: All tests pass ✅
```

---

## ✅ Phase 3: Marketplace API - COMPLETE

### Implemented Features:
1. ✅ 6 API endpoints added to `rest_routes.py` (no new files, maximum code reuse)
   - GET `/v1/marketplace/collections` - Browse marketplace with search & pagination
   - POST `/v1/marketplace/collections/<id>/subscribe` - Subscribe to collections
   - DELETE `/v1/marketplace/subscriptions/<id>` - Unsubscribe from collections
   - POST `/v1/marketplace/collections/<id>/fork` - Fork collections
   - POST `/v1/rag/collections/<id>/publish` - Publish to marketplace
   - POST `/v1/marketplace/collections/<id>/rate` - Rate and review collections

2. ✅ Test suite created (`test/test_marketplace_phase3.py`)
   - All 6 tests passing (100% coverage)
   - Publish collection ✅
   - Browse marketplace ✅
   - Subscribe/unsubscribe ✅
   - Fork collection ✅
   - Rate collection ✅

### Verification:
```bash
# Run Phase 3 test suite
python test/test_marketplace_phase3.py
# Expected: All 6 tests pass ✅
```

---

## ✅ Phase 4: Marketplace UI - SHIPPED

The Marketplace view (sidebar → Marketplace) now serves seven tabs (Planner,
Knowledge, Knowledge Graphs, Agent Packs, Extensions, Skills, Components) through
`static/js/handlers/marketplaceHandler.js`. Original task list:
1. Add marketplace browse panel to Intelligence pane
2. Subscribe/unsubscribe buttons in UI
3. Fork collection modal dialog
4. Publish collection workflow
5. Rating and review display
6. Search and filtering interface

---

**Documentation:**  
- Architecture (all product types): [`docs/Architecture/MARKETPLACE_ARCHITECTURE.md`](../Architecture/MARKETPLACE_ARCHITECTURE.md)
- Rating system: [`RATING_SYSTEM_IMPLEMENTATION.md`](RATING_SYSTEM_IMPLEMENTATION.md)
- (The earlier `MARKETPLACE_IMPLEMENTATION.md` / `PHASE_*_COMPLETE.md` files were retired.)
