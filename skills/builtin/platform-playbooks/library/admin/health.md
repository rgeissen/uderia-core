# How is the deployment doing

The whole deployment at a glance — consumption across users, the database and backups, maintenance jobs, providers — in totals, never anyone's conversations.

**Use for:** "how is the deployment doing", "how many users were active this month", "is the database healthy", "when was the last backup", "did the maintenance jobs run", "is a model provider failing".

## Steps

1. call `consumption.system_summary` — active users, tokens, estimated cost, failed turns and the models in use, all users together.
2. call `admin.stats` — how many users, active, locked, by tier; recent sign-ins and registrations (counts only).
3. call `admin.db_health` — its `state`, unreadable reads (`count`, `last_seen`), the last backup and how many are kept.
4. call `admin.platform_jobs` with `fields: ["name", "schedule", "enabled", "last_run_at", "last_run_status"]` — the platform's own jobs.
5. When something did not run, call `scheduler.status`; when a model fails, call `llm.provider_health`.
6. When the user asks about prices or cost, call `costs.coverage` with `fields: ["provider", "model", "priced"]` — the models in use that have no price (their spend counts as zero).

## Reading the result

- A database `state` other than `ok`, or no backup in the last day: name it first.
- A platform job whose `last_run_status` is an error, or that has not run on schedule: name the job and when it last ran.
- A provider whose breaker is open: its models are failing over or failing — say which.

## What to tell the user

Totals first, then what needs attention: *"This month: 14 active users, 9.2M tokens, $41 estimated. The database is healthy; the last backup ran at 02:30. The price sync failed last night — 2 models now run unpriced."*

## Do not

- Name or count a single user's activity — the view is the deployment's, in totals.
- Run a backup or a job — report what is due; the administrator acts.
- Say something is fine that you did not read — name what you checked, and say what you did not.
