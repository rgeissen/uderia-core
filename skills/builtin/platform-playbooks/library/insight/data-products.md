# What the data products can answer

Explain which data products a knowledge graph offers, what each measures and how far it is trusted, and show the SQL a request would run — without running it.

**Use for:** "which data products are on the Sales graph", "what can I ask the revenue product", "is the orders product validated", "what SQL would revenue by region last quarter run", "why does my request to the product fail".

## Steps

1. call `knowledge_graph.list` with `fields: ["kg_id", "kg_name", "profile_tag"]` and agree which graph (the agent it belongs to helps the user recognise it).
2. call `data_products.catalog` with `kg_id`. Each product carries its name (`product`), `description`, `trust` and whether a validation backs it (`validated`).
3. For one product in full: call `data_products.get` with `product_name` and `kg_id` — its measures, dimensions, and which combinations are safe to ask.
4. For a request: call `data_products.compile` with `product_name`, `kg_id` and `request` (e.g. `{"measures": ["revenue"], "dimensions": ["region"], "time_grain": "quarter"}`). It returns the SQL; nothing runs.

## Reading the result

- `trust` not signed, or `validated: false`: the product may not be offered to agents that only use signed products — say so.
- A refused compile names the closest valid measures and dimensions; offer them.

## What to tell the user

What the product answers and how trusted it is, then the SQL when asked: *"The Sales graph offers 'revenue' (signed, validated) and 'orders' (draft). Revenue by region for last quarter would run: SELECT …"* To get the numbers, the user asks an agent that reads data products.

## Do not

- Run the SQL or present numbers — reading data is an agent's own access, under its own authority.
