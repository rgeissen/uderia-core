# Dashboard Expert

The component handles all mechanics automatically: panel opening, tile positioning, chart type selection from data shape, and refresh execution. Focus your decisions on **what to show**, **how to word the query**, and **how to compose the layout**.

---

## visualize vs add_tile

Use **`visualize`** any time you are presenting data. The component renders a chart inline with "Add to Dashboard" and "Open Dashboard" buttons — no dashboard is created or opened automatically.

Use **`add_tile`** only when the user explicitly says *save*, *pin*, or *add to dashboard* — and only when a dashboard already exists.

---

## Content: pass the table, not the prose

The `content` arg is the chart's data source. If your answer contains both a prose summary and a markdown table, pass **only the table**. The auto-viz engine needs column structure to build axes — prose alone renders as text with no chart.

```
# Good — tabular content
content: "| Customer | Revenue |\n|---|---|\n| Mikhail Smirnov | $6,999 |\n..."

# Bad — prose without a table
content: "The top 5 customers are Mikhail Smirnov ($6,999), Lena Fischer ($5,200)..."
```

Only use `visualize` when your answer has a table, a number series, or a list of (label, value) pairs. For pure text answers, return the text directly — don't force a chart.

---

## Query: write the refresh contract

The `query` arg is re-executed every time the tile refreshes. Write it as a **self-contained natural language question** that will return the same data type weeks from now.

```
# Good — self-contained, refreshes correctly
query: "Top 5 customers by total revenue this year"

# Bad — references context that won't exist at refresh time
query: "These results"
query: "The data above"
query: "What we just discussed"
```

For `add_tile`, the `query` you write is the only refresh signal — make it precise.

---

## chart_type: omit it unless the user specified one

The component detects chart type automatically from the `query` and `tile_title` keywords (pie, bar, line, scatter, trend). Only pass `chart_type` when the user **explicitly** requested a style. Valid values: `pie`, `bar`, `column`, `line`, `scatter`.

```
chart_type: "pie"      ← user said "as a pie chart"
chart_type: "line"     ← user said "as a line chart"
# omit entirely       ← user said "show me revenue by region"
```

A well-worded `tile_title` like "Revenue by Region (Pie)" or "Sales Trend Over Time" gives the auto-viz engine a strong signal even without `chart_type`.

---

## Tile sizing grid (`add_tile` only)

The dashboard uses a 12-column grid. Each row is approximately 80px tall.

| Content type | tile_width | tile_height | Notes |
|---|---|---|---|
| KPI / single metric | 3 | 2 | Four across the top row |
| Bar / line chart | 6 | 4 | Two side by side |
| Wide trend chart | 12 | 4 | Full width, one per row |
| Data table | 12 | 6 | Always full width for readability |
| Pie chart | 4 | 4 | Three across |
| Text / narrative | 6 | 3 | Half-width summary panel |

Default (width=6, height=4) is the right starting point for most charts. Only specify width/height when deliberately composing a layout.

---

## Visual composition patterns

**KPI row + chart body** — most common:
1. 3–4 KPI tiles (width=3, height=2) for at-a-glance numbers across the top
2. 1–2 chart tiles (width=6, height=4) below for trends
3. One table tile (width=12, height=6) at the bottom for detail

**Side-by-side comparison:** Two charts at width=6 comparing related metrics (e.g. revenue vs cost)

**Full-width focus:** One chart at width=12 when the trend is the whole story

---

## Tile titles

Concise and descriptive. The title is the only label visible on the canvas tile.

**Good:** `Revenue by Region`, `Open Tickets by Priority`, `Daily Active Users`
**Bad:** `Chart 1`, `Data`, `Results`
**Never:** emoji

---

## Anti-patterns

- **Don't vary `query` between the initial call and refresh** — keep it stable so the tile refreshes consistently.
- **Don't make all tiles the same size** — vary widths to create visual hierarchy.
- **Don't exceed 8–10 tiles per dashboard** — split by theme instead.
- **Don't use vague tile titles** — the title must identify the data without reading it.
