# What the deployment costs

Where the deployment's spend goes, and where it cannot be counted: cost by model and provider, the trend, and the models running without a price.

**Use for:** "what are we spending", "which model costs the most", "is cost going up", "which models run without a price", "why does a model show zero cost".

## Steps

1. call `consumption.system_summary` for the deployment's estimated cost and the models in use, all users together.
2. call `costs.coverage` with `fields: ["provider", "model", "priced"]` — which models in use have no price. An unpriced model's spend reads as zero.
3. For prices: call `costs.models` with `fields: ["provider", "model", "input_cost_per_million", "output_cost_per_million", "source"]` (the list is long — name the provider or model you are looking for).
4. For the administrator's own conversations by model and over time: call `costs.analytics` — it covers their own conversations, not other users'.

## Reading the result

- An unpriced model makes every total lower than the truth — say so next to the total.
- A price whose `source` is a fallback or a manual entry is an estimate; name it when it drives the total.

## What to tell the user

The spend and its blind spots: *"$41 this month across all users, most of it Gemini Flash Lite. Celeris-1 runs unpriced, so its share counts as zero — the real total is higher."*

## Do not

- Fill in prices or change one — report what is missing; the administrator sets prices.
