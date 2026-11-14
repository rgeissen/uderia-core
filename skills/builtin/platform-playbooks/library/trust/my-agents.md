# Trust across my agents

Which of the user's agents are trusted, changed, unsigned or not attested here — one overview.

**Use for:** "which of my agents are signed", "what needs signing", "show my trust overview".

## Steps

1. call `trust.agents`. Each record has `tag`, `ifoc` (the class), `state` and `composition_state`; `counts` holds the platform's own figures — `total` and the agents per `state` and per `ifoc`.

## Reading the result

- The figures come from `counts`, never from counting the records yourself: its `total`, and its `state` per group — trusted · changed · not attested here (`deployment_unattested`) · unsigned.
- `changed` and not attested here need attention first: they were signed, and something moved since. `unsigned` is a choice the user may have made on purpose.
- A record whose `blocking` is present names what stops it; read it only when the user asks why.
- The answer may come back summarised (summary fields only): the counts still hold; for the reason behind one agent, use the playbook `trust/explain-agent`.

## What to tell the user

Counts first (from `counts`), worst first: *"Of your N agents, 2 changed since they were signed (@SALES, @OPS), 1 is not attested here (@TCOPT), the others are trusted or unsigned — each group with its figure."* Then name the agents in the groups that need attention. List all agents only if asked.

## Do not

- Call one agent "the" problem when several share a state.
- Present unsigned as a failure.
