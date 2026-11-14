# When did it get worse, and what changed

Find when an agent's quality, cost or latency moved, and line it up with what changed around then — its model, its configuration, its signature, its knowledge.

**Use for:** "@SALES got worse this week", "when did quality drop", "why is @DOCS slower", "did my change break @COORD", "is @OPTIM regressing".

## Steps

1. call `evals.agent_timeline` with `tag` (and `bucket: "day"`, or `"hour"` for a recent change). Each entry of `timeline` has a `label` (the day), `pass_rate`, `turns`, `avg_cost` and `avg_latency_ms`. Find the first step where the value moved and stayed moved.
2. call `assurance.trust_history` with `tag` — every signature, attestation, change and renewal, with when.
3. call `assurance.agent_story` with `tag` — when it was built, changed, what its gates caught.
4. Around the step: call `evals.agent_runs` with `tag` and `runs: 20`, and compare `failed_dimensions` before and after.

## Reading the result

- A change in `trust_history` within a day of the step is the first suspect; name it with its time.
- A step with very few `turns` either side is not a regression yet — say so.
- Cost rising with the same pass rate usually means longer contexts or a different model; call `profiles.overview` with the agent's tag for its model.

## What to tell the user

When, how much, and the likely cause with its evidence: *"@SALES fell from 92% to 61% on Oct 30; its configuration changed on Oct 30 at 14:02 (components updated). The new failures are all `grounding`."* If nothing changed, say that too.

## Do not

- Claim a cause the records do not show — say "nothing in its history changed then" when that is the case.
