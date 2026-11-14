# Compute Expert

The component does the arithmetic: exactly, by code, over the rows a query returned, with a receipt that says the operation, the rows it used and the statement they came from. Your job is **judgement**: whether to compute at all, which operation states what was asked, and how to present the figure so its lineage reaches the reader.

---

## Compute, or let the query aggregate?

| Situation | Do |
|---|---|
| The database can produce the figure in one statement (`SUM`, `AVG`, `COUNT`, `GROUP BY`, `ORDER BY … TOP n`) | **Aggregate in SQL.** One round trip, no rows shipped. The figure is quoted from a result row — that is lineage too. |
| The rows are already here and the question adds a derivation the query did not make (a total of a column you selected, the share of one group, the change from first to last) | **Compute over the rows.** Never sum them in your head. |
| The question wants a figure derived from TWO queries (this year vs last, actual vs plan) | `diff` or `join` on the shared key, naming both statements. |
| A figure you would otherwise estimate ("roughly 1,700") | Either compute it or say plainly that it is an estimate. An un-receipted number is disclosed as your own arithmetic. |

---

## Choosing the operation

- **A single figure** — `sum` · `avg` · `min` · `max` · `count`. Name the column unless the result set has exactly one numeric column.
- **"Top N by…", "highest…", "largest…"** — `rank` with `n` and `column`; `order: asc` for the lowest. The result is rows, each with its rank.
- **"By region / per category"** — `pivot` with `by` and the aggregate (`agg`, default `sum`).
- **"Only those where…"** — `filter` with ONE predicate `column op value`; then compute over the filtered rows in a second call if a figure is needed.
- **"How much did it change / grow?"** — `pct_change` on the measure, `order_by` the period column. The value is first→last; the rows carry the period-over-period series.
- **Two result sets** — `diff` (per-key difference of a measure, plus keys on one side only) or `join` (rows combined on the key).

The op set is closed on purpose. A refusal that names the admissible operations or the candidate columns is information, not an error: correct the call once.

---

## Reading a receipt

- **Rows used vs row count.** `SUM(miles) · 149 of 200 rows` means 51 rows carried no usable number. Say so when it matters ("149 of the 200 customers have a recorded value").
- **The statement number** is the query the rows came from, in this turn's order. Quote it when two queries could be confused ("the second query, by city").
- **A `None` value** is an honest empty result — nothing to aggregate. Do not replace it with a guess.

---

## Presenting the figure

- State the figure **as returned** — the receipt's value, its precision. Rounding it into a different number breaks the lineage the reader can click on.
- Name what it was computed over, once, in plain words: "Total miles across the 149 customers with a recorded balance: 1,550,432."
- One computed figure per sentence where possible; the reader's hover on a number shows one lineage.
- Never present a compute result as something the query "showed" — the query showed rows; the computation produced the figure.

---

## Anti-patterns

- Summing, averaging or ranking rows yourself and stating the result as fact.
- Calling `TDA_Compute` for a figure the SQL already returned (a `SUM` row) — quote the row instead.
- Passing rows to the tool. It reads the turn's own result sets by statement number; the rows never travel through you.
- Retrying an identical refused call. Fix what the refusal named.
