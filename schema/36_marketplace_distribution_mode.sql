-- Migration 36: Add distribution_mode to marketplace_agent_packs
-- Controls which acquisition options are available to consumers:
--   'both'           (default) — Subscribe and Fork both available
--   'subscribe_only' — only Subscribe available
--   'fork_only'      — only Fork available

ALTER TABLE marketplace_agent_packs
    ADD COLUMN distribution_mode TEXT NOT NULL DEFAULT 'both';
