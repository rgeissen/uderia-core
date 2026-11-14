# Try a question against a knowledge repository

See what an agent would retrieve from a knowledge repository for a question — which documents answer it and how well — without asking the agent.

**Use for:** "what does the Policies repository find for travel expenses", "does my documentation cover SSO", "test the knowledge base with this question", "why didn't my agent find the refund rule".

## Steps

1. call `rag.collections` with `light: true` and `fields: ["id", "name", "repository_type"]` when the user has not named the repository (only `repository_type: knowledge`).
2. call `knowledge.search` with `collection_id` (its name works), `query` set to the user's question as they asked it, and `k: 5`.

## Reading the result

- Each passage carries its `content`, its `distance` (lower is closer; an agent's similarity is `1 - distance`) and in its `metadata` the `filename` of the document it comes from. Several passages from one document: that document carries the answer.
- Nothing found, or only distant passages: the repository does not cover the question — that is why an agent answering from it could not.
- An agent drops a passage whose similarity is below its `minRelevanceScore` — a distance above `1 - minRelevanceScore` — and keeps at most its `maxDocs`; to see an agent's settings, call `profiles.overview` with its tag. So an agent may use fewer passages than this search shows.

## What to tell the user

The documents that answer and whether the coverage is good: *"Policies finds the refund rule in Customer Terms.pdf (two passages, close match). Travel expenses only match loosely — nothing in it covers them."* Offer adding a document (they need it in Files first) when coverage is missing.

## Do not

- Answer the user's question from the passages yourself as if you were the agent — report what the repository finds.
