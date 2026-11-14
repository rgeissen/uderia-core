# My usage this period

What the user has spent this period — tokens, cost, turns, limits — and where it went.

**Use for:** "how much have I used", "what did I spend this month", "am I close to my limits", "which questions cost the most".

## Steps

1. call `consumption.summary`.
2. Only when the user asks where the cost went turn by turn: call `consumption.turns` with `limit: 20`.
3. Only when the user asks about an earlier period: call `consumption.history`.

## Reading the result

- The period: `period_started_at`. Cost: `estimated_cost_usd`. Volume: `total_turns`, `total_tokens` (`total_input_tokens` + `total_output_tokens`).
- Limits: a `*_limit` of `null` means no limit; otherwise compare with the matching `*_remaining` / `*_today` and say how close the user is.
- Where it went: `models_used` (turns per model), `model_distribution` (share per model), `top_expensive_questions`.
- Quality: `success_rate_percent`, `failed_turns`.
- `rag_cost_saved_usd` is what reused planning saved — mention it only when asked about efficiency.

## What to tell the user

Cost and turns first, then the limit closest to being reached, then the biggest driver: *"Since 1 October you have run 2,868 turns for about $4.20. No limit is close. Most of the cost came from Nova 2 Lite (54% of turns)."*

## Do not

- Round a cost to zero or present an estimate as an invoice: say "about".
- Report another user's usage — this playbook covers the caller only.
