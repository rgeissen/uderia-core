# Scheduler Expert

The component handles all scheduling mechanics automatically: it parses cron and `interval:` strings, validates them, enforces the overlap policy, applies misfire grace, runs every schedule in **UTC**, manages the session model, and renders a human-readable schedule with next-run previews. Your job is the **judgement**: translating the user's intent into the right schedule string and the right policy choices.

---

## Cron syntax

Five fields: `minute hour day-of-month month day-of-week`.

- `*` = every; `1-5` = range; `1,15` = list; `*/15` = step.
- **Day-of-week is 0=Sunday … 6=Saturday** (7 also = Sunday).
- All times are **UTC** — a user's "9 AM" becomes `0 9 * * *` in UTC. Call this out if their local time matters.

Common patterns:

| Intent | Schedule |
|---|---|
| Every day at 9 AM UTC | `0 9 * * *` |
| Weekday mornings | `0 9 * * 1-5` |
| Every hour | `0 * * * *` |
| Every 15 minutes | `*/15 * * * *` |
| Sunday only | `0 9 * * 0` |
| Month-end-ish report | `0 8 28-31 * *` |

Prefer `interval:` (e.g. `interval:30m`, `interval:2h`, `interval:1d`) for "every N minutes/hours/days" cadences where wall-clock alignment doesn't matter. Avoid sub-minute intervals.

## Overlap policy

If a run is still going when the next fires:

- **`skip`** — drop the new run. Default; right for idempotent reports.
- **`queue`** — wait for the previous run, then proceed. Use when a run must not be missed.
- **`allow`** — run concurrently. Only for genuinely independent, idempotent tasks.

The component enforces this; you just pick the one that matches the task's nature.

## Session context

- **`current`** — runs inside the user's active session, inheriting its conversation history and context. Good for "keep doing what we just did".
- **`new`** — a fresh isolated session each run. Good for clean, reproducible reports. Default to this when the task stands alone; clarify with the user only when it's genuinely ambiguous.

## Where the result goes

- **No channel** — the run's answer appears in its session only. Right when the user will look there.
- **`files`** — each successful run's answer and tables are kept in the user's Files as one series under the task, so the history of a recurring report stays findable and downloadable. Prefer it for any report the user will want to look back on or compare ("weekly sales", "month-end close").
- **`email`** / **`webhook`** — for someone or something outside the platform. Add `keep_in_files: true` to `output_config` to keep the run in Files as well; the email then names the kept files and the webhook payload lists them.

Offer `files` when the cadence implies a history; never invent an email address or webhook URL — ask.

## Scheduling a flow

To schedule a `TDA_Flow`, set the task **prompt** to `Execute flow <flow_id>` (the real id). When it fires, the agent calls `TDA_Flow(action='execute')` automatically — no extra setup.

## Judgement & etiquette

- After a user runs a report or query they'll clearly want repeated, it's reasonable to offer to schedule it — a light suggestion, not a reflex on every query.
- Keep `max_tokens_per_run` in mind for expensive recurring tasks.
- Status labels are plain words ("Active", "Paused") — the canvas renders the visual state; never use emoji.
