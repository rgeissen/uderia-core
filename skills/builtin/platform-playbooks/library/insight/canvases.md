# Keep canvases tidy

Find, rename, roll back or remove the user's canvases — the documents and code kept from conversations — each change submitted for the user's approval.

**Use for:** "which canvases do I have", "rename the SQL draft canvas", "go back to the previous version of my report canvas", "remove the old canvases", "show me the canvas about churn".

## Steps

1. call `canvases.list` with `fields: ["canvas_id", "title", "language", "updated_at"]` and agree which canvas.
2. To show it: call `canvases.get` with `canvas_id`.
3. The change:
   - **Rename:** call `canvases.update` with `canvas_id` and `title`.
   - **Roll back:** call `canvases.versions` with `canvas_id`, agree which version, then call `canvases.version_restore` with `canvas_id` and `version_id`.
   - **Remove:** call `canvases.delete` with `canvas_id` — always approved by the user.

## Reading the result

- `status: awaiting_approval`: waiting in the user's Assurance Inbox.

## What to tell the user

The change in one sentence: *"I've submitted restoring 'Q3 report' to its version from Tuesday 14:10."*

## Do not

- Write a canvas's content — that is done in a conversation with the canvas component.
