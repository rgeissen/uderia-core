# Explore a knowledge graph

What one of the user's knowledge graphs holds: its entity types and how they connect, the entities a question is about, and what sits next to one of them.

**Use for:** "what is in my Policing graph", "which tables does the Retail graph know", "what is Customer connected to", "how big is the Sales ontology", "which business concepts does my graph define", "which graphs do I have".

## Steps

1. call `knowledge_graph.list` with `fields: ["kg_id", "kg_name", "profile_tag", "total_entities"]` when the user has not named the graph, and agree which one. Every later step takes the graph's name as `kg_id`.
2. The shape: call `knowledge_graph.schema_overview` with `kg_id` — its entity types, how many of each, and which types connect to which. For the totals alone, call `knowledge_graph.stats`.
3. The entities a question is about: call `knowledge_graph.entities` with `kg_id`, `search` (words from the request) and, when the user names a kind, `type` (`table`, `column`, `business_concept`, `metric`, `data_product`, …).
4. What one entity is connected to: call `knowledge_graph.node_neighborhood` with `kg_id` and the entity's `entity_id` from step 3. For the wider area around it, call `knowledge_graph.subgraph` with `entity_name` and a small `max_nodes` (50).

## Reading the result

- A type with thousands of entities (columns, usually): name the count, never list them — search within it instead.
- An entity found under two types (a table and a business concept with the same name): say which one you mean.
- `knowledge_graph.list` empty: the user has no graph yet — graphs are built from the Graph constructor, not here.

## What to tell the user

The shape first, then what they asked about: *"The Policing graph holds 3,867 entities — 214 tables, 3,402 columns, 38 business concepts, 12 metrics. Incident is a business concept backed by the INCIDENT and INCIDENT_PARTY tables, and connects to Officer, Location and Offence."*

## Do not

- Read a graph entity by entity to describe it — the overview is one call.
- Say a graph lacks something because a search found nothing; say what you searched for.
