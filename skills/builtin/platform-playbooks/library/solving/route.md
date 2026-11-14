# Answer a question that spans several fields

Get a question answered that no single expert answers alone: ask the ones that fit each part and combine their answers, naming who answered what and what remains unanswered.

**Use for:** "who can answer this question", "answer this from my data and my documents together", "why did claims rise where incidents fell", "combine what my data expert and my policy expert know".

## Steps

1. Split the question into parts, each one a field: a figure from data, a rule from documents, a fact about the platform. A part about the user's agents, trust or usage you answer yourself with the platform's operations — no agent needs asking for it.
2. Who could answer each part: call `profiles.list` with `fields: ["tag", "name", "profile_type", "description"]`. For a candidate whose fit is unclear, call `profiles.overview` with its tag — its data source, repositories and graph say what it can know.
3. How far to trust each candidate: call `trust.agents` with `fields: ["tag", "state"]`; when two could answer, prefer the signed one, and call `evals.kpi_dashboard` for their pass rates.
4. Ask: use the action `ask` with `agent` (the @TAG) and `question` — complete in itself, because the agent does not see this conversation: name the period, the region, the measure. One ask per part; never ask the same agent the same thing twice.
5. Put the answers together.

## Reading the result

- A refusal (asking off, an agent you may not ask, a limit, an agent not activated) is final for this turn: name it as the reason that part is unanswered.
- An answer that says it does not know is an answer: report it as a gap, never fill it in.
- Two agents' figures that disagree: give both, with who said which; do not average them.

## What to tell the user

Part by part, each with its source: *"Claims rose 12% in the North (@CLAIMS, from the claims warehouse). Incidents fell 8% there (@POLICING). Why the two moved apart neither agent could say — @POLICY found no rule change in that period."*

## Do not

- Present an asked agent's answer as your own reading.
- Ask an agent for something the platform's own operations already answered.
