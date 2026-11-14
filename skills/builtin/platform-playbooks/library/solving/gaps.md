# What is missing to answer a question

When no one can answer a question, find out what is missing — an expert or the data itself — and what would close the gap.

**Use for:** "nobody can answer this", "what would I need to answer this", "what am I missing to answer this", "build something that can answer this".

## Steps

1. What each agent covers: call `profiles.list` with `fields: ["tag", "name", "profile_type", "description"]`.
2. What the user holds that no agent uses: call `mcp.servers` with `fields: ["id", "name"]` (data sources), call `rag.collections` with `light: true` and `fields: ["id", "name", "repository_type"]` (repositories), and call `knowledge_graph.list` with `fields: ["kg_id", "kg_name", "profile_tag"]` (graphs).
3. Decide which gap it is:
   - **An agent is missing** — a source, repository or graph holds what the question needs, and no agent is built on it. Describe the agent that would close it: its class (Focus for documents, Optimize for data, Ideate for reasoning), what it would be built on, and a tag.
   - **The data is missing** — nothing the user holds covers it. Name what would: a repository to create, a source to connect.
4. When this agent also holds the Agent Builder role and the user wants the agent built: follow the Agent Builder's playbook and call `profiles.create` — it is submitted for the user's approval.

## Reading the result

- A new agent cannot be asked until it is approved, tested and activated — by the user. Say so: the question can be asked again once it is.
- A repository or graph assigned to no agent is the usual gap.

## What to tell the user

*"None of your agents covers claims. Your 'Claims DW' data source has the tables; an Optimize agent on it (@CLAIMS) would answer this. I submitted it for your approval — once you activate it, ask me again."*

## Do not

- Build an agent when the data itself is missing — say what is missing instead.
- Say an agent cannot answer without having read what it is built on.
