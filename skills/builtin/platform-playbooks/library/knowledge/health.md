# Check a knowledge repository's health

How sound a knowledge repository is: what it holds, whether its documents are screened, what the screening found, what is quarantined, and which documents the user rated badly.

**Use for:** "is my Policies repository healthy", "what's wrong with the documentation repository", "which documents in Sales are rated badly", "are there security findings in my knowledge".

## Steps

1. call `rag.collections` with `light: true` and `fields: ["id", "name", "repository_type", "document_count"]` when the user has not named the repository, and agree which one (only `repository_type: knowledge`).
2. call `knowledge.health` with `collection_id` (its name works). Read `total_documents`, `scanned`, `unscreened`, `coverage_pct`, `injection_documents`, `exfil_urls`, `pii_manifest` (labels and counts — never values) and `quarantined`.
3. call `knowledge.feedback` with `collection_id` for `lowest_rated` — the documents behind the answers the user rated worst.
4. Only when a finding or a rating names documents: call `knowledge.documents` with `collection_id` and `fields: ["document_id", "filename", "chunk_count"]` to put names to them.

## Reading the result

- `unscreened` above 0: those documents were never checked — screening them is a change the Knowledge Curator submits; offer it.
- `injection_documents`: documents carrying text that tries to steer an agent. They need the owner's judgement: quarantine, delete, or acknowledge with a reason.
- `pii_manifest`: personal data by label (`email`, `phone`, …) and count.
- `lowest_rated` empty: the user has not rated answers from this repository yet — say so; it is not a clean bill.

## What to tell the user

Summary first, then what needs them: *"Policies holds 42 documents, all screened. Two carry injection-like text (Vendor FAQ.pdf, Old Terms.docx) and one was rated poorly three times (Travel Rules.md). Want me to quarantine the two flagged ones?"*

## A person's act

Acknowledging a finding (`knowledge.document_acknowledge_finding`) and releasing a quarantine (`knowledge.document_release`) are the user's.

## Do not

- Report a personal-data value — only labels and counts exist here, keep it that way.
- Call a repository healthy when part of it is unscreened.
