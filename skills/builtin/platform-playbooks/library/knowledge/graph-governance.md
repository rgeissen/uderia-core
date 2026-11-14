# A graph's versions, lifecycle and conformance

Where a knowledge graph stands — its saved versions, its lifecycle, whether it conforms, the tuning proposed for it — and saving a version before it changes.

**Use for:** "is my Policing graph ready to publish", "which versions of the Retail graph do I have", "save the graph before I change it", "is my ontology conformant", "who approved the Sales ontology", "are there tuning proposals for my graph", "which of my ontologies are published".

## Steps

1. call `knowledge_graph.list` with `fields: ["kg_id", "kg_name", "profile_tag"]` when the user has not named the graph. Every later step takes the graph's name as `kg_id`.
2. Where it stands: call `knowledge_graph.lifecycle` with `kg_id` — its state (draft → proposed → approved → published → deprecated), version and audit trail.
3. Whether it conforms: call `knowledge_graph.conformance` with `kg_id` — the issues and their severity. The check changes nothing.
4. Its versions: call `knowledge_graph.versions` with `kg_id`.
5. To save one: call `knowledge_graph.version_save` with `kg_id` and a short `label`. Nothing is saved when the graph has not changed since the last version — say so.
6. Tuning: call `knowledge_graph.tuning_proposals` with `kg_id` (and `status: "pending"` for what waits on the user).
7. The user's published ontologies, all together: call `ontologies.list`.

## Reading the result

- Publishing needs a conformant graph: high-severity issues block it — name them first.
- `lifecycle_status: "published"` with a newer draft: what agents and BI tools read is the published version, not the draft.
- A tuning proposal `pending` waits on the user; `failed` landed nothing.

## What to tell the user

State, then what blocks the next step: *"Policing is a draft, version 0.4, conformant with 2 low-severity issues. You saved it last on 12 October ('before PII columns'). Proposing it for approval is yours, from the graph's card."*

## A person's act

Moving the lifecycle (`knowledge_graph.lifecycle_transition`), signing the ontology (`trust.ontology_sign_off`), restoring or deleting a version (`knowledge_graph.version_restore`, `knowledge_graph.version_delete`), deleting the graph (`knowledge_graph.clear`), rebuilding it from its source (`knowledge_graph.apply_update`), deciding a tuning proposal (`knowledge_graph.tuning_decide`) and every deployment to the database are the user's.

## Do not

- Save a version unasked when the user only wanted to know where the graph stands.
- Call a graph ready to publish without reading its conformance.
