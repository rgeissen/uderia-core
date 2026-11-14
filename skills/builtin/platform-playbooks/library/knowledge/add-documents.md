# Add documents to a knowledge repository

Put documents the user has in their Files into one of their knowledge repositories — creating the repository first when they want a new one — and submit it for their approval.

**Use for:** "add the handbook to the Sales repository", "put my uploaded PDFs into a knowledge repository", "create a repository for our policies", "make a new knowledge base from these files".

## Steps

1. call `files.list` with `fields: ["id", "filename", "can_knowledge"]` (add `q` with words from the name when the user names a file). Only a file with `can_knowledge: true` can be added — a PDF, Word, Markdown or text document. A file the user has not uploaded yet: ask them to add it in Files first; you cannot upload it.
2. call `files.knowledge_targets` for the repositories it can go into — the user's own knowledge repositories.
3. A new repository, only when the user wants one: agree its name and one sentence on what it holds, then call `rag.collection_create` with `name`, `repository_type: "knowledge"` and `description`. It is **submitted for approval** — the repository does not exist until the user approves it, so stop here and tell them to ask again for the documents once it does.
4. For each document: call `files.to_knowledge` with `file_id` and `collection_id` (its name works). Each is **submitted for approval** — nothing is added yet.

## Reading the result

- `status: awaiting_approval`: waiting in the user's Assurance Inbox; once approved the document is screened, chunked and embedded by the repository's own upload.
- "Only documents … can be added": that file is a table, image or export — say so and skip it.
- "no repository of yours has the id or name …": take one from step 2.

## What to tell the user

What will be added where, and that it waits for them: *"I've submitted adding Policy Handbook.pdf and Travel Rules.md to the Policies repository — two requests in your Assurance Inbox. Agents answering from Policies will find them once you approve; the repository's signature will read changed until you sign it again."*

## A person's act

Approving, choosing where a document syncs from (`knowledge.document_source`), and signing the repository (`trust.knowledge_sign_off`) are the user's. Never attempt them.

## Do not

- Add a document twice — one request per document, then wait.
- Create a repository the user did not ask for.
