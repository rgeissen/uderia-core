# Keep dashboards in order

Create, rename, arrange or remove the user's dashboards and their tiles, and refresh a tile through its own agent — each change submitted for the user's approval.

**Use for:** "make a dashboard called Weekly Sales", "rename my dashboard", "refresh the revenue tile", "move the chart to the top", "make the map tile wider", "remove the old tile", "remove the test dashboard", "what is on my dashboards".

## Steps

1. call `dashboards.list` with `fields: ["id", "name", "tile_count"]` and agree which dashboard (its name works only to find it — pass its `id`).
2. Its tiles: call `dashboards.tiles` with `dashboard_id` — each tile's `title`, its agent (`profile_tag`) and how its last refresh went (`refresh_status`, `last_refreshed`).
3. The change:
   - **A new, empty dashboard:** call `dashboards.create` with `name` and `description`. Tiles are pinned from conversations with the dashboard component — say so.
   - **Rename or re-describe:** call `dashboards.update` with `dashboard_id` and only what changes.
   - **A tile's title, place or size:** call `dashboards.tile_update` with `dashboard_id`, `tile_id` and only what changes; several tiles at once: call `dashboards.layout` with `tiles`.
   - **Refresh a tile:** call `dashboards.tile_refresh` with `dashboard_id` and `tile_id` — the tile's own agent answers again (it spends tokens).
   - **Remove a tile:** call `dashboards.tile_delete`; a whole dashboard: call `dashboards.delete` — always approved by the user.

Each is **submitted for approval** unless the agent may run changes itself.

## Reading the result

- `status: awaiting_approval`: waiting in the user's Assurance Inbox.
- A tile whose `refresh_status` is an error: name the tile and its agent; refreshing again does not fix a question that no longer works — the user edits the tile's question.

## What to tell the user

The change in one sentence: *"I've submitted moving 'Revenue by region' to the top row of Weekly Sales and making it full width."*

## Do not

- Add a tile or change what a tile shows or asks — pinning and editing a tile's question are the user's.
- Change how often a dashboard refreshes on its own.
