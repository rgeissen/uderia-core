# What is failing, and why

Which quality checks fail, for which agents, and what the failing turns have in common — read from the scores the platform records on every turn.

**Use for:** "which checks fail most", "why is @SALES failing", "what is wrong with my agents", "which agent has the worst quality", "show me failing turns for @DOCS".

## Steps

1. call `evals.kpi_dashboard`. Read `overall` (`pass_rate`, `turns`, `fail_turns`), `failing_checks` (each `dimension` with its `fail_rate` over `n` turns) and `agents` (each `tag` with its `pass_rate`, `turns` and `status`).
2. For the agent the user asks about, or the one doing worst: call `evals.agent_runs` with `tag` and `runs: 10`. Each run carries `failed_dimensions` and `verdicts` — each verdict's `dimension`, `passed` and `explanation` says why.
3. To see one failing turn in full: call `sessions.details` with its `session_id` and `fields: ["session_history", "last_turn_data"]`.
4. When the user needs to know what a check measures: call `assurance.vocabulary` with `fields: ["key", "label", "question"]`.

## Reading the result

- A check failing on most turns of one agent but not others points at that agent's setup; failing everywhere points at the check's threshold or the model.
- `completed` failing means the turn itself failed (an error, a provider refusal) — look at the turn, not the answer.
- An agent with few `turns` has a noisy pass rate — say how many turns it rests on.

## What to tell the user

The worst first, with its evidence: *"@IDEAT passes 0 of its 12 scored turns — every one fails `completed` (the turns errored). Across all agents `instruction_following` fails most (82% of 67 turns)."* Then one concrete next step.

## Do not

- Re-score anything to answer this — the recorded scores are the answer.
- Call a pass rate good or bad without the number of turns behind it.
