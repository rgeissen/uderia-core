# Inspect an agent

What one agent is and what it can use: its class, model, data source, components, skills and trust state, in a short answer.

**Use for:** "what is @SALES", "what can @SALES do", "how is @SALES configured", "which components does @SALES have".

## Steps

1. call `profiles.overview` with `profile_id: "@TAG"`. One call answers it all: class, model, data source, components, skills, trust state, whether it is the default and whether it is open to the REST API.
2. Only when the question is about data or tools: call `profiles.resources` with `profile_id: "@TAG"` and count the tools per category.

## Reading the result

- `class` is already the name to use (Ideate, Focus, Optimize, Coordinate).
- `components` are the ones it uses. Files, Tool Finder and Agent Memory are on for most agents; mention them only when asked. `system_components` only when asked.
- Under `skills`, `active` are in effect on every turn; `when_asked` are used when the user asks for them.
- `data_source: null` means it reaches no database; say so when the question is about data.
- Under `trust`, `state` in words: trusted (signed, unchanged since), changed (signed, something it is built from changed since), unsigned (never signed), deployment_unattested (signed, not attested on this deployment). For why, the playbook `trust/explain-agent`.
- `not_read` names a part the platform could not read: say which part is missing, never present it as empty.
- `description` is the owner's own words — quote it, do not improve it.

## What to tell the user

One sentence first: *"@SALES is an Optimize agent on Gemini 3.1 Flash Lite that reaches the Sales database; it uses Charting and Knowledge Graph, has two active skills and is trusted."* Then, only if the user asked for detail, a short table: Components · Skills · Trust.

## Do not

- Name ids, configuration ids or internal type names.
- List every component when the user asked what the agent is.
