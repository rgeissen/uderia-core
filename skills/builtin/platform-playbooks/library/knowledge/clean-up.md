# Clean up a knowledge repository

Keep a repository's content right — quarantine or remove a wrong document, sync, screen, change settings — each submitted for the user's approval.

**Use for:** "quarantine the vendor FAQ", "remove the old terms from Policies", "take that document out of the repository", "sync the documentation repository", "screen my repository", "rename the Sales repository", "change how often Policies syncs".

## Steps

1. call `knowledge.documents` with `collection_id` (its name works) and `fields: ["document_id", "filename", "chunk_count"]`, and agree with the user which document is meant. Never act on a document the user has not confirmed.
2. The change:
   - **Out of retrieval, reversibly:** call `knowledge.document_quarantine` with `document_id` and a `reason`. Prefer this when the user is unsure — only they can release it again.
   - **Gone:** call `knowledge.document_delete` with `collection_id` and `document_id`. Always reviewed; not reversible except by adding it again.
   - **Bring in step with its sources:** call `knowledge.sync` with `collection_id`.
   - **Screen it:** call `knowledge.scan` with `collection_id` (`rescan: true` to screen everything again).
   - **Settings:** call `knowledge.repository_settings` with `collection_id` and only what changes (`description`, `sync_interval`, `sensitivity`); a new name or search mode: call `rag.collection_update`.
   - **The whole repository:** call `rag.collection_delete` with `collection_id` — only when the user says so in as many words; conversations that used it are archived.

Each is **submitted for the user's approval** — it has not run.

## Reading the result

- `status: awaiting_approval`: waiting in the Assurance Inbox.
- `guardrails master switch is off`: screening needs the deployment's guardrails; say so.

## What to tell the user

The change and what follows from it: *"I've submitted quarantining Vendor FAQ.pdf from Policies. Once you approve, no agent retrieves it; it stays listed, and only you can release it. Policies' signature will read changed until you sign it again."*

## A person's act

Releasing a quarantine, acknowledging a finding, choosing where a document syncs from, locking a planner repository and signing are the user's. Never attempt them.

## Do not

- Delete when quarantine would do and the user has not asked for deletion.
- Submit the same change twice.
