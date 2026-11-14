# What runs on its own

What the user has automated — flows and scheduled questions — and how their recent runs went.

**Use for:** "what is scheduled", "which automations do I have", "did the morning report run", "how did my flows do this week", "why did the daily summary fail", "is the scheduler running".

## Steps

1. call `scheduled_tasks.list` with `fields: ["id", "name", "schedule", "enabled", "profile_id"]` — each task also carries `_human_schedule` (its schedule in words) and `_next_runs`.
2. call `flows.list` with `fields: ["flow_id", "name", "description"]`.
3. For one task's history: call `scheduled_tasks.runs` with `task_id` and `fields: ["status", "started_at", "result_summary", "cost_usd"]`.
4. For one flow's history: call `flows.runs` with `flow_id` and `fields: ["run_id", "status", "started_at", "cost_usd"]`; a failed run's detail: call `flows.run_status` with its `run_id`.
5. Nothing ran when it should have: call `scheduler.status` — `running` and `globally_enabled` both true means the scheduler is working.

## Reading the result

- A task with `enabled: 0` is paused.
- Run `status`: `success`, `error`, `timeout` or `skipped` (`skip_reason` says why — usually the previous run was still going).

## What to tell the user

The automations in one short list, then what needs attention: *"Two scheduled questions (Morning orders — weekdays 09:00, paused; Weekly summary — Mondays 08:00, last run succeeded) and one flow (Quarterly answer, last run failed at @DOCS)."*

## Do not

- Change anything here — this is a reading; a change goes through the Automation Builder.
