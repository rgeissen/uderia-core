# Schedule a question for an agent

Have one of the user's agents answer a question on a timer — every weekday morning, every hour — and pause, resume, change or remove such a schedule. Every schedule is approved by the user.

**Use for:** "every weekday at 9 ask @SALES for yesterday's orders", "schedule a daily summary from @DOCS", "run this every hour", "pause the morning report", "change the schedule to Mondays", "stop the daily summary".

## Steps

1. call `profiles.list` with `fields: ["tag", "name", "profile_type"]` when the user has not named the agent.
2. Agree three things: the agent, the question exactly as the agent should receive it each time, and when. Write the schedule as a 5-field cron (`0 9 * * 1-5` = weekdays at 09:00, `0 8 * * 1` = Mondays at 08:00) or an interval (`interval:1h`, `interval:30m`, `interval:1d`).
3. call `scheduled_tasks.create` with `name`, `profile_id` (the agent's tag), `prompt` and `schedule`; add `max_tokens_per_run` when the user named a limit. It is **always submitted for approval** — an agent acting on its own on a timer is the user's decision.
4. An existing schedule: call `scheduled_tasks.list` with `fields: ["id", "name", "schedule", "enabled"]`, then
   - pause or resume: call `scheduled_tasks.update` with `task_id` and `enabled` false or true
   - change it: call `scheduled_tasks.update` with `task_id` and only what changes
   - once now: call `scheduled_tasks.run_now` with `task_id`
   - remove it: call `scheduled_tasks.delete` with `task_id` — prefer pausing when the user may want it back.

## Reading the result

- `status: awaiting_approval`: waiting in the Assurance Inbox.
- `component_not_available`: the scheduler is switched off for this user — an administrator's setting.

## What to tell the user

The schedule in words and that it waits for them: *"I've submitted 'Morning orders' — @SALES answers 'How many orders came in yesterday?' every weekday at 09:00. It starts once you approve it; results appear in a new session each run."*

## A person's act

Approving the schedule, and choosing to have results sent by email or webhook, are the user's — set delivery in the Scheduler panel.

## Do not

- Schedule more often than the user asked.
- Change a schedule's question or agent when they only asked to pause it.
