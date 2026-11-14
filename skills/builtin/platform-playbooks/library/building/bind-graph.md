# Give an agent a knowledge graph

Choose which knowledge graph an agent reads: assign a graph to it, make it the agent's active graph, or take it away — each submitted for the user's approval.

**Use for:** "let @SALES use the Retail graph", "which agents have the Policing graph", "make the new ontology the one @OPS reads", "stop @HR using the Staff graph", "which graph does @SALES read".

## Steps

1. call `knowledge_graph.list` with `fields: ["kg_id", "kg_name", "profile_tag"]` and agree the graph. Every later step takes its name as `kg_id`.
2. Who has it now: call `knowledge_graph.assignments` with `kg_id` — each agent and whether it is that agent's active graph.
3. To assign or take away: call `knowledge_graph.assignments_set` with `kg_id` and `assigned_profile_ids` — the COMPLETE list: everyone from step 2 who keeps it, plus or minus the change. A short list removes the agents it leaves out.
4. To make it the graph an agent reads: call `knowledge_graph.activate` with `kg_id` and `assigned_profile_id`. The agent's other graphs stay assigned but inactive.
5. Once approved and applied: call `knowledge_graph.context` with the agent as `profile_id` and a question in the user's words — what the agent would now be told from its graph.

## Reading the result

- Both changes wait for the user's approval in the Assurance Inbox — say that they were submitted, not that they are done.
- An agent can have several graphs assigned but reads only its active one.
- `knowledge_graph.context` empty after approval: the graph holds nothing that matches the question — not a failed assignment.

## What to tell the user

What will change and what waits on them: *"The Retail graph is assigned to @OPS today. I submitted assigning it to @SALES as well, and making it @SALES's active graph — both are in your Inbox."*

## Do not

- Send `assigned_profile_ids` without reading the current assignments first.
- Touch the graph's content — which agents read it is all this changes.
