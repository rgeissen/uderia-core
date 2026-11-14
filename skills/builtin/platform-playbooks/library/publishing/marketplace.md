# What is in the marketplace

Find packs others have published, read what one offers, and see what it would need from the user's data sources — without installing anything.

**Use for:** "which packs are about policing", "what is new in the marketplace", "what does the Policing pack contain", "what would the Policing pack need to run here", "who is my pack offered to".

## Steps

1. call `marketplace.agent_packs` with `search` (words from the user's request) and `fields: ["id", "name", "description", "publisher_username", "version"]`; `sort_by: "rating"` or `"installs"` when the user asks for the best or most used.
2. For one pack: call `marketplace.agent_pack` with `pack_id` — its agents, ratings and versions.
3. What it would need here: call `marketplace.agent_pack_requirements` with `pack_id` — each agent's data-source needs resolved against the user's own.
4. For a pack the user published: call `marketplace.agent_pack_audience` with `pack_id` to see who it is offered to.

## Reading the result

- A requirement the user's data sources do not meet: that agent would install but not run until a matching source exists — name it.
- `visibility: "targeted"` lists packs offered to the user specifically; `"all"` lists both.

## What to tell the user

What fits and what it would take: *"Two packs match 'policing': Policing Data Products (4 agents, rated 4.6) and Case Triage (2 agents). Policing Data Products needs a Teradata source named like yours — three of its agents would run as they are."*

## A person's act

Installing, forking, subscribing, rating and choosing a pack's audience are the user's (`marketplace.agent_pack_install`, `marketplace.agent_pack_fork`, `marketplace.agent_pack_rate`, `marketplace.agent_pack_audience_set`).
