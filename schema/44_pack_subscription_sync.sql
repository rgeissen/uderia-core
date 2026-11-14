-- Migration 44: pack-subscription sync stamps
-- content_updated_at: when a listing's CONTENT last moved (publish, publisher update_in_place).
--   updated_at is not a content signal: every subscribe and fork bumps it with install_count.
-- synced_at / sync_error: a subscriber install's last successful sync and last failure.
-- Applied idempotently at startup by auth/database.py:_add_pack_subscription_sync_columns
-- (PRAGMA-guarded ALTERs + backfill), the same way 36 is applied.

ALTER TABLE marketplace_agent_packs
    ADD COLUMN content_updated_at TEXT;

ALTER TABLE agent_pack_installations
    ADD COLUMN synced_at TEXT;

ALTER TABLE agent_pack_installations
    ADD COLUMN sync_error TEXT;
