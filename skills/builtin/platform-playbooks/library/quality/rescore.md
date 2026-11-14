# Score a turn again, or try a policy

Check one turn again against what a right answer says (optionally with model judges), or see what a stricter trust policy would have done to past turns — before anyone switches it on.

**Use for:** "was this answer right — it should say 42", "score the last turn of that conversation again", "judge that answer", "what would enforce grounding have done to @SALES", "if I turned on stricter checks, what would change".

## Steps

1. The conversation: call `sessions.list` with `fields: ["id", "name", "profile_tag", "turn_count"]` and agree which one (and which turn — the last by default).
2. **Score it again:** call `evals.score_turn` with `session_id`, `turn`, and `reference_answer` (what a right answer says) or `assertions` (e.g. `{"must_contain": ["42"]}`). Add `include_judges: true` only when the user wants model judges — it costs tokens, so it waits for approval unless the agent may run changes.
3. **Try a policy:** call `evals.backtest` with `policy` (e.g. `{"grounding": "enforce"}`) and, for one agent, `profile_tag`. It replays recorded turns at no cost and changes nothing.

## Reading the result

- `evals.score_turn` returns a verdict per check; a failed one carries its `explanation`. The turn's recorded scores do not change.
- `evals.backtest` counts the turns that would have been withheld, flagged, or sent to a judge; a judge is not simulated, so "would go to the judge" is not a certain block.

## What to tell the user

The verdict and its reason, or the policy's effect in counts: *"Against your reference the answer fails `correctness`: it says 41, not 42."* / *"Enforcing grounding on @SALES would have withheld 3 of its last 120 answers and sent 2 to a judge."*

## A person's act

Changing an agent's posture or the deployment's policy is the owner's — this only shows what it would do.

## Do not

- Ask for judges when the user did not — deterministic checks cost nothing.
